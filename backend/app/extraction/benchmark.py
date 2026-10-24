"""Reproducible, provider-agnostic evaluation on labelled local splits.

Commands::

    # Development comparisons: validation split only
    python -m backend.app.extraction.benchmark run --split validation --provider donut
    python -m backend.app.extraction.benchmark run --split validation --provider ocr --parser tesseract
    python -m backend.app.extraction.benchmark compare reports/bench-validation-donut.json \\
        reports/bench-validation-ocr.json

    # Freeze a configuration, then run the held-out test split exactly once
    python -m backend.app.extraction.benchmark freeze reports/bench-validation-ocr.json
    python -m backend.app.extraction.benchmark run --split test --provider ocr --parser tesseract \\
        --final-test-run --frozen reports/frozen-ocr.json

Every provider goes through ``extract_validated`` with the same canonical schema. The
report stores, per document: the source row, the provider/model revision, the
prompt/config, the full output record, the expected values and per-field matches,
findings, latency and token usage. Aggregates include exact and normalized per-field
accuracy, line-item precision/recall/F1 (strict and normalized), coverage, invalid
response rate, whole-document accuracy, latency, peak memory, token usage, and 95%
Wilson or bootstrap intervals (n is small; read the intervals, not just the means).

Leakage guards: retrieval (``--rag``) indexes the training split only, and the test
split requires ``--final-test-run`` with a frozen configuration whose fingerprint (code,
provider, prompt, parser, limits) must match. Each configuration may be run on the test
split once (recorded in ``reports/test-runs.jsonl``).
"""

import argparse
import hashlib
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .dataset import DATASET_ID, load_local_split
from .evaluate import evenly_spaced_indices
from .evaluation import (FIELD_PATHS, _value, aggregate_item_prf, evaluate_examples,
                         evaluate_predictions)
from .matching import evidence_form
from .schema import normalize_donut_result

REPORT_VERSION = "benchmark-1"
NUMERIC_FIELDS = {"total_amount", "subtotal", "tax_amount", "line_item_quantities",
                  "line_item_unit_prices", "line_item_net_amounts", "line_item_vat_amounts",
                  "line_item_gross_amounts", "line_item_amounts", "line_item_tax_rate_percent"}
# Dataset party labels combine name and address; compare the combined text.
LABEL_FIELDS = {"vendor_label": "vendor", "customer_label": "customer"}
# Legacy fields kept in the exact metric for comparability with historical reports, but
# not in the normalized metric: "vendor"/"customer" compare a name with a combined
# name+address label (replaced by the *_label fields) and "line_item_amounts" is the
# deprecated gross-first field (covered by line_item_gross_amounts).
NORMALIZED_EXCLUDE = {"vendor", "customer", "line_item_amounts"}
CODE_FILES = ("document_input.py", "evidence.py", "local_ocr.py", "matching.py",
              "ocr_mapping.py", "providers.py", "schema.py", "validation.py", "vision_api.py",
              "donut.py", "rag.py")
TEST_RUN_LOG = Path("reports/test-runs.jsonl")


# --- statistics ---------------------------------------------------------------------

def wilson(successes: int, total: int, z: float = 1.959964) -> dict:
    """95% Wilson score interval for a proportion (well behaved at small n and 0/1)."""
    if total == 0:
        return {"estimate": None, "low": None, "high": None, "n": 0}
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {"estimate": round(p, 4), "low": round(max(0.0, centre - margin), 4),
            "high": round(min(1.0, centre + margin), 4), "n": total}


def bootstrap(values_per_doc: list, statistic, resamples: int = 2000, seed: int = 7) -> dict:
    """Percentile bootstrap over documents (documents are the independent units)."""
    if not values_per_doc:
        return {"estimate": None, "low": None, "high": None, "n": 0}
    rng = random.Random(seed)
    estimate = statistic(values_per_doc)
    samples = []
    for _ in range(resamples):
        draw = [values_per_doc[rng.randrange(len(values_per_doc))] for _ in values_per_doc]
        value = statistic(draw)
        if value is not None:
            samples.append(value)
    samples.sort()
    if not samples:
        return {"estimate": estimate, "low": None, "high": None, "n": len(values_per_doc)}
    low = samples[int(0.025 * (len(samples) - 1))]
    high = samples[int(math.ceil(0.975 * (len(samples) - 1)))]
    rounded = lambda v: None if v is None else round(v, 4)
    return {"estimate": rounded(estimate), "low": rounded(low), "high": rounded(high),
            "n": len(values_per_doc), "resamples": resamples, "seed": seed}


def _micro(counts):
    correct = sum(c[0] for c in counts)
    scored = sum(c[1] for c in counts)
    return correct / scored if scored else None


def _f1(counts):
    matched = sum(c[0] for c in counts)
    predicted = sum(c[1] for c in counts)
    expected = sum(c[2] for c in counts)
    precision = matched / predicted if predicted else None
    recall = matched / expected if expected else None
    if not precision or not recall:
        return 0.0 if expected else None
    return 2 * precision * recall / (precision + recall)


# --- normalized comparison ----------------------------------------------------------

def _num(value):
    try:
        return None if value is None else round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _normalized_equal(field, predicted, expected) -> bool:
    if isinstance(expected, list):
        if not isinstance(predicted, list) or len(predicted) != len(expected):
            return False
        return all(_normalized_equal(field, p, e) for p, e in zip(predicted, expected))
    if field in NUMERIC_FIELDS:
        return _num(predicted) is not None and _num(predicted) == _num(expected)
    return evidence_form(predicted) != "" and evidence_form(predicted) == evidence_form(expected)


def _party_label(record, role):
    party = record.get(role) or {}
    parts = [party.get("name"), party.get("address")]
    text = " ".join(str(part) for part in parts if part)
    return text or None


def _unlabelled(gold):
    return gold is None or gold == [] or (isinstance(gold, list) and all(g is None for g in gold))


def document_scores(prediction: dict, expected: dict) -> dict:
    """Per-field exact/normalized matches for one document (scored fields only)."""
    from .evaluation import _equal
    fields = {}
    for field, path in FIELD_PATHS.items():
        gold = _value(expected, path)
        if _unlabelled(gold):
            continue
        guess = _value(prediction, path)
        fields[field] = {"expected": gold, "predicted": guess,
                         "exact": _equal(field, guess, gold),
                         "normalized": (None if field in NORMALIZED_EXCLUDE
                                        else _normalized_equal(field, guess, gold)),
                         "predicted_present": guess is not None and guess != []}
    for field, role in LABEL_FIELDS.items():
        gold = _party_label(expected, role)
        if gold is None:
            continue
        guess = _party_label(prediction, role)
        fields[field] = {"expected": gold, "predicted": guess,
                         "exact": guess is not None and " ".join(guess.split()).casefold()
                         == " ".join(gold.split()).casefold(),
                         "normalized": _normalized_equal(field, guess, gold),
                         "predicted_present": guess is not None}
    return fields


def normalized_item_counts(predicted_items, expected_items):
    """Greedy one-to-one item matching on alphanumeric description + gross amount."""
    def key(item):
        amount = item.get("gross_amount") if item.get("gross_amount") is not None else item.get("amount")
        return evidence_form(item.get("description")), _num(amount)
    remaining = [key(item) for item in expected_items]
    matched = 0
    for item in predicted_items:
        k = key(item)
        for index, candidate in enumerate(remaining):
            if candidate[0] and candidate[0] == k[0] and (candidate[1] is None or candidate[1] == k[1]):
                remaining.pop(index)
                matched += 1
                break
    return matched, len(predicted_items), len(expected_items)


# --- resources ------------------------------------------------------------------------

def peak_memory_mb() -> dict:
    """Peak resident memory of this process and of finished child processes (OCR)."""
    try:
        import resource
        scale = 1024 * 1024 if sys.platform == "darwin" else 1024
        own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale
        children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / scale
        return {"process_peak_mb": round(own, 1), "child_process_peak_mb": round(children, 1),
                "method": "resource.getrusage"}
    except ImportError:
        pass
    try:
        import psutil
        info = psutil.Process().memory_info()
        return {"process_peak_mb": round(getattr(info, "peak_wset", info.rss) / 1048576, 1),
                "child_process_peak_mb": None, "method": "psutil"}
    except ImportError:
        return {"process_peak_mb": None, "child_process_peak_mb": None,
                "method": "unavailable (install psutil on Windows)"}


def _summary(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(math.ceil(0.9 * len(ordered))) - 1)]
    return {"mean": round(statistics.fmean(values), 3), "median": round(statistics.median(values), 3),
            "p90": round(p90, 3), "max": round(max(values), 3), "n": len(values)}


# --- configuration fingerprint ------------------------------------------------------

def code_fingerprint() -> str:
    digest = hashlib.sha256()
    base = Path(__file__).parent
    for name in CODE_FILES:
        path = base / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    return digest.hexdigest()[:16]


def config_fingerprint(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]


def build_config(settings, provider, parser_engine_version, rag_k) -> dict:
    return {
        "report_version": REPORT_VERSION, "code": code_fingerprint(),
        "provider": provider.name, "model_revision": provider.revision,
        "prompt_version": getattr(provider, "prompt_version", None),
        "parser_engine": settings.parser_engine, "parser_engine_version": parser_engine_version,
        "max_page_pixels": settings.max_page_pixels, "pdf_render_dpi": settings.pdf_render_dpi,
        "max_pages": settings.max_pages, "ocr_language": settings.ocr_language,
        "vision_max_image_pixels": settings.vision_max_image_pixels if settings.provider == "vision-api" else None,
        "vision_send_parser_text": settings.vision_send_parser_text if settings.provider == "vision-api" else None,
        "rag_k": rag_k,
    }


# --- run ------------------------------------------------------------------------------

def run_benchmark(provider, settings, split: str, data_root: Path, limit: int = None,
                  parser=None, parser_version=None, retriever=None, rag_k: int = 0,
                  document_loader=None) -> dict:
    from .document_input import load_document
    from .providers import InvalidModelResponse, extract_validated

    examples, split_dir = load_local_split(data_root, split)
    positions = evenly_spaced_indices(len(examples), limit or len(examples))
    selected = [examples[i] for i in positions]
    loader = document_loader or (lambda path: load_document(
        path, settings.max_pages, settings.max_page_pixels, settings.pdf_render_dpi))
    config = build_config(settings, provider, parser_version, rag_k if retriever else 0)
    rows, documents, failures = [], [], []
    started_all = time.perf_counter()
    for example in selected:
        source = {"dataset": DATASET_ID, "split": split, "row_index": example["row_index"],
                  "file": example["image_path"].name}
        expected = normalize_donut_result(example["ground_truth"]).to_dict()
        t0 = time.perf_counter()
        document = loader(example["image_path"])
        t1 = time.perf_counter()
        parse = parser(document) if parser is not None else None
        t2 = time.perf_counter()
        context = None
        if retriever is not None:
            context = retriever.retrieve(parse.full_text() if parse else "", rag_k)
        try:
            result = extract_validated(provider, document, dict(source, source_file=source["file"]),
                                       context=context, parse=parse)
        except InvalidModelResponse as exc:
            failures.append({**source, "error": str(exc)})
            documents.append({"source": source, "invalid_response": True, "error": str(exc),
                              "expected_items": len(expected.get("items") or []),
                              "timing_seconds": {"load": round(t1 - t0, 3), "parse": round(t2 - t1, 3),
                                                 "extract": round(time.perf_counter() - t2, 3)}})
            continue
        t3 = time.perf_counter()
        record = result["record"]
        diagnostics = result["provenance"].get("generation_diagnostics") or {}
        scores = document_scores(record, expected)
        item_strict = _item_triplet(record.get("items") or [], expected.get("items") or [])
        item_norm = normalized_item_counts(record.get("items") or [], expected.get("items") or [])
        documents.append({
            "source": source, "invalid_response": False,
            "validation_status": result["validation_status"],
            "flagged": result["validation_status"] != "valid_reconciled",
            "pages": len(getattr(document, "pages", []) or [1]),
            "timing_seconds": {"load": round(t1 - t0, 3), "parse": round(t2 - t1, 3),
                               "extract": round(t3 - t2, 3), "total": round(t3 - t0, 3)},
            "usage": diagnostics.get("usage"), "cost_estimate": diagnostics.get("cost_estimate"),
            "retrieval_ids": result["provenance"].get("retrieval_ids"),
            "fields": scores, "items_strict": item_strict, "items_normalized": item_norm,
            "findings": [f["code"] for f in result["findings"]],
            "output": record, "expected": expected,
        })
        rows.append({"ground_truth": example["ground_truth"], "prediction": record, "source": source})
    elapsed = time.perf_counter() - started_all
    report = {
        "report_version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET_ID, "split": split, "local_split_directory": str(split_dir),
        "selected_row_indices": [e["row_index"] for e in selected],
        "config": config, "config_fingerprint": config_fingerprint(config),
        "runtime": {"python": sys.version.split()[0], "platform": platform.platform(),
                    "machine": platform.machine(), "cpu_count": os.cpu_count(),
                    "elapsed_seconds": round(elapsed, 2), **peak_memory_mb()},
        "aggregate": aggregate(documents),
        "legacy_metrics": evaluate_predictions(rows) if rows else None,
        "legacy_item_prf": aggregate_item_prf(rows) if rows else None,
        "failures": failures,
        "documents": documents,
        "limitations": [
            "Measured on the stated rows only; small n gives wide intervals.",
            "Dataset invoices are synthetic and share one template family; results do not "
            "establish accuracy on other layouts or real documents.",
            "Party labels combine name and address; vendor/customer label fields compare the "
            "combined text, and the legacy exact 'vendor'/'customer' fields penalise a correct split.",
            "Latency and memory depend on the machine recorded under runtime.",
        ],
    }
    report["examples"] = evaluate_examples(rows) if rows else []
    return report


def _item_triplet(predicted, expected):
    from .evaluation import item_prf
    result = item_prf(predicted, expected)
    return result["matched"], result["predicted"], result["expected"]


def aggregate(documents: list) -> dict:
    valid = [d for d in documents if not d["invalid_response"]]
    total = len(documents)
    fields = {}
    names = sorted({name for d in valid for name in d["fields"]})
    for name in names:
        scored = [d["fields"][name] for d in valid if name in d["fields"]]
        missing_docs = sum(1 for d in documents if d["invalid_response"])
        exact = sum(1 for s in scored if s["exact"])
        normalized = sum(1 for s in scored if s["normalized"])
        present = sum(1 for s in scored if s["predicted_present"])
        n = len(scored) + missing_docs  # an invalid response counts as wrong
        excluded = bool(scored) and all(s["normalized"] is None for s in scored)
        fields[name] = {"scored": n, "exact": wilson(exact, n),
                        "normalized": None if excluded else wilson(normalized, n),
                        "coverage": round(present / n, 4) if n else None}

    def doc_counts(kind):
        per_doc = []
        for d in documents:
            if d["invalid_response"]:
                continue
            values = [s[kind] for s in d["fields"].values() if s[kind] is not None]
            per_doc.append((sum(1 for v in values if v), len(values)))
        return per_doc

    exact_docs = sum(1 for d in valid if d["fields"] and all(s["exact"] for s in d["fields"].values()))
    norm_docs = sum(1 for d in valid if d["fields"] and all(
        s["normalized"] is not False for s in d["fields"].values()))
    wrong = [d for d in valid if any(s["normalized"] is False for s in d["fields"].values())]
    flagged = [d for d in valid if d["flagged"]]
    usage_in = [((d.get("usage") or {}).get("input_tokens")) for d in valid]
    usage_out = [((d.get("usage") or {}).get("output_tokens")) for d in valid]
    costs = [(d.get("cost_estimate") or {}).get("amount") for d in valid]
    pages = [d["pages"] for d in valid]
    return {
        "documents": total, "valid_responses": len(valid),
        "invalid_response_rate": wilson(total - len(valid), total),
        "micro_field_accuracy_exact": bootstrap(doc_counts("exact"), _micro),
        "micro_field_accuracy_normalized": bootstrap(doc_counts("normalized"), _micro),
        "document_accuracy_exact": wilson(exact_docs, total),
        "document_accuracy_normalized": wilson(norm_docs, total),
        # An invalid response recovers none of its expected rows.
        "line_items_strict": _item_summary([d["items_strict"] if not d["invalid_response"]
                                            else (0, 0, d.get("expected_items", 0)) for d in documents]),
        "line_items_normalized": _item_summary([d["items_normalized"] if not d["invalid_response"]
                                                else (0, 0, d.get("expected_items", 0)) for d in documents]),
        "fields": fields,
        "review_flags": {
            "flagged_documents": len(flagged),
            "wrong_given_flagged": wilson(sum(1 for d in flagged if d in wrong), len(flagged)),
            "flagged_given_wrong": wilson(sum(1 for d in wrong if d["flagged"]), len(wrong)),
            "wrong_but_not_flagged": len([d for d in wrong if not d["flagged"]]),
            "definition": "flagged = validation_status != valid_reconciled; wrong = any scored "
                          "field not normalized-equal",
        },
        "status_counts": _counts(d.get("validation_status") for d in valid),
        "latency_seconds_per_document": _summary([d["timing_seconds"]["total"] for d in valid]),
        "latency_seconds_per_page": _summary([d["timing_seconds"]["total"] / max(p, 1)
                                              for d, p in zip(valid, pages)]),
        "parse_seconds": _summary([d["timing_seconds"]["parse"] for d in valid]),
        "extract_seconds": _summary([d["timing_seconds"]["extract"] for d in valid]),
        "tokens": {"input_total": sum(v for v in usage_in if v) or None,
                   "output_total": sum(v for v in usage_out if v) or None},
        "cost_estimate_total": round(sum(c for c in costs if c), 6) if any(costs) else None,
    }


def _counts(values):
    result = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _item_summary(triplets):
    matched = sum(t[0] for t in triplets)
    predicted = sum(t[1] for t in triplets)
    expected = sum(t[2] for t in triplets)
    return {"matched": matched, "predicted": predicted, "expected": expected,
            "precision": wilson(matched, predicted), "recall": wilson(matched, expected),
            "f1": bootstrap([t for t in triplets if t[2] or t[1]], _f1)}


# --- comparison ----------------------------------------------------------------------

def compare(report_a: dict, report_b: dict, resamples: int = 2000, seed: int = 11) -> dict:
    """Paired comparison on identical rows: B minus A with a paired bootstrap interval."""
    rows_a = {d["source"]["row_index"]: d for d in report_a["documents"]}
    rows_b = {d["source"]["row_index"]: d for d in report_b["documents"]}
    if report_a["split"] != report_b["split"] or set(rows_a) != set(rows_b):
        raise ValueError("reports must cover the same split and rows")
    keys = sorted(rows_a)

    def accuracy(doc, kind):
        values = [s[kind] for s in (doc.get("fields") or {}).values() if s[kind] is not None]
        if doc["invalid_response"] or not values:
            return (0, max(len(values), 1))
        return (sum(1 for v in values if v), len(values))

    result = {"split": report_a["split"], "rows": len(keys),
              "a": {"provider": report_a["config"]["provider"], "fingerprint": report_a["config_fingerprint"]},
              "b": {"provider": report_b["config"]["provider"], "fingerprint": report_b["config_fingerprint"]}}
    rng = random.Random(seed)
    for kind in ("exact", "normalized"):
        pairs = [(accuracy(rows_a[k], kind), accuracy(rows_b[k], kind)) for k in keys]

        def diff(sample):
            a = _micro([p[0] for p in sample])
            b = _micro([p[1] for p in sample])
            return None if a is None or b is None else b - a
        estimate = diff(pairs)
        samples = sorted(v for v in (diff([pairs[rng.randrange(len(pairs))] for _ in pairs])
                                     for _ in range(resamples)) if v is not None)
        result[f"micro_field_accuracy_{kind}_difference"] = {
            "estimate": round(estimate, 4) if estimate is not None else None,
            "low": round(samples[int(0.025 * (len(samples) - 1))], 4) if samples else None,
            "high": round(samples[int(math.ceil(0.975 * (len(samples) - 1)))], 4) if samples else None,
            "interpretation": "B - A; an interval that includes 0 is not evidence of a difference"}
    better = sum(1 for k in keys if accuracy(rows_b[k], "normalized")[0] / accuracy(rows_b[k], "normalized")[1]
                 > accuracy(rows_a[k], "normalized")[0] / accuracy(rows_a[k], "normalized")[1])
    worse = sum(1 for k in keys if accuracy(rows_b[k], "normalized")[0] / accuracy(rows_b[k], "normalized")[1]
                < accuracy(rows_a[k], "normalized")[0] / accuracy(rows_a[k], "normalized")[1])
    result["documents_better_worse_same"] = [better, worse, len(keys) - better - worse]
    return result


# --- markdown ---------------------------------------------------------------------------

def _ci(value):
    if not value or value.get("estimate") is None:
        return "n/a"
    return f"{value['estimate']:.1%} (95% CI {value['low']:.1%}–{value['high']:.1%}, n={value['n']})"


def markdown(report: dict) -> str:
    agg = report["aggregate"]
    config = report["config"]
    lines = [f"# Benchmark: {config['provider']} on {report['split']}", "",
             f"- Rows: {agg['documents']} ({report['selected_row_indices'][:5]}...)" if len(report['selected_row_indices']) > 5
             else f"- Rows: {agg['documents']} {report['selected_row_indices']}",
             f"- Configuration fingerprint: `{report['config_fingerprint']}` (code `{config['code']}`)",
             f"- Model revision: `{config['model_revision']}`; parser: {config['parser_engine']} "
             f"{config['parser_engine_version'] or ''}",
             f"- Machine: {report['runtime']['platform']}, {report['runtime']['cpu_count']} CPUs; "
             f"peak memory {report['runtime'].get('process_peak_mb')} MB (children "
             f"{report['runtime'].get('child_process_peak_mb')} MB)", "",
             "| Metric | Result |", "|---|---|",
             f"| Micro field accuracy, exact | {_ci(agg['micro_field_accuracy_exact'])} |",
             f"| Micro field accuracy, normalized | {_ci(agg['micro_field_accuracy_normalized'])} |",
             f"| Whole-document accuracy, exact | {_ci(agg['document_accuracy_exact'])} |",
             f"| Whole-document accuracy, normalized | {_ci(agg['document_accuracy_normalized'])} |",
             f"| Line items F1, strict | {_ci(agg['line_items_strict']['f1'])} |",
             f"| Line items F1, normalized | {_ci(agg['line_items_normalized']['f1'])} |",
             f"| Line items precision / recall, normalized | {_ci(agg['line_items_normalized']['precision'])} / "
             f"{_ci(agg['line_items_normalized']['recall'])} |",
             f"| Invalid response rate | {_ci(agg['invalid_response_rate'])} |",
             f"| Wrong documents not flagged for review | {agg['review_flags']['wrong_but_not_flagged']} |",
             f"| Latency per page (median / p90) | "
             f"{(agg['latency_seconds_per_page'] or {}).get('median')} s / "
             f"{(agg['latency_seconds_per_page'] or {}).get('p90')} s |",
             f"| Tokens in / out | {agg['tokens']['input_total']} / {agg['tokens']['output_total']} |",
             "", "## Per field", "", "| Field | Exact | Normalized | Coverage |", "|---|---|---|---|"]
    for name, values in agg["fields"].items():
        normalized = _ci(values["normalized"]) if values["normalized"] else "not scored (legacy)"
        coverage = f"{values['coverage']:.0%}" if values["coverage"] is not None else "n/a"
        lines.append(f"| `{name}` | {_ci(values['exact'])} | {normalized} | {coverage} |")
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"


# --- CLI --------------------------------------------------------------------------------

def _build(settings, args):
    from ..pipeline import build_parser, build_provider
    provider = build_provider(settings)
    parser = build_parser(settings)
    version = None
    if settings.parser_engine != "none":
        from .local_ocr import build_engine
        version = getattr(build_engine(settings.parser_engine, settings), "version", None)
    retriever = None
    if args.rag:
        if not getattr(provider, "consumes_prompt", False) or settings.provider != "vision-api":
            raise SystemExit("--rag needs a provider that reads text context (vision-api)")
        from .rag import TrainingRetriever
        train, _ = load_local_split(args.data_root, "train")
        retriever = TrainingRetriever(train)
    return provider, parser, version, retriever


def _guard_test_run(args, config_fp):
    if args.limit:
        raise SystemExit("The final test run uses every row of the test split; drop --limit.")
    if not args.final_test_run or not args.frozen:
        raise SystemExit("The test split is held out: pass --final-test-run and --frozen FILE "
                         "(created by 'freeze' from a validation report).")
    frozen = json.loads(Path(args.frozen).read_text(encoding="utf-8"))
    if frozen.get("config_fingerprint") != config_fp:
        raise SystemExit("Current configuration differs from the frozen one "
                         f"({config_fp} != {frozen.get('config_fingerprint')}); refusing the test run.")
    if TEST_RUN_LOG.is_file():
        for line in TEST_RUN_LOG.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("config_fingerprint") == config_fp:
                raise SystemExit("This frozen configuration was already run on the test split; "
                                 "the test split is used once per configuration.")


def main(argv=None):
    from ..config import Settings
    import dataclasses

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--split", choices=("validation", "test"), default="validation")
    run_p.add_argument("--provider", choices=("donut", "ocr", "vision-api"), required=True)
    run_p.add_argument("--parser", choices=("none", "tesseract", "rapidocr"))
    run_p.add_argument("--limit", type=int, help="evenly spaced rows (default: all rows)")
    run_p.add_argument("--data-root", type=Path, default=Path("data/raw/invoices-donut-data-v1"))
    run_p.add_argument("--output", type=Path)
    run_p.add_argument("--rag", action="store_true", help="training-split retrieval (vision-api only)")
    run_p.add_argument("--rag-k", type=int, default=3)
    run_p.add_argument("--final-test-run", action="store_true")
    run_p.add_argument("--frozen", type=Path)
    freeze_p = sub.add_parser("freeze")
    freeze_p.add_argument("report", type=Path)
    freeze_p.add_argument("--output", type=Path)
    compare_p = sub.add_parser("compare")
    compare_p.add_argument("a", type=Path)
    compare_p.add_argument("b", type=Path)
    args = parser.parse_args(argv)

    if args.command == "freeze":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        if report["split"] != "validation":
            raise SystemExit("Freeze a validation report, not a test report.")
        frozen = {"frozen_at": datetime.now(timezone.utc).isoformat(), "from_report": str(args.report),
                  "config": report["config"], "config_fingerprint": report["config_fingerprint"],
                  "validation_summary": {k: report["aggregate"][k] for k in (
                      "micro_field_accuracy_normalized", "document_accuracy_normalized",
                      "line_items_normalized")}}
        output = args.output or args.report.with_name(f"frozen-{report['config']['provider'].replace(':', '-')}.json")
        output.write_text(json.dumps(frozen, indent=2), encoding="utf-8")
        print(f"Frozen configuration {report['config_fingerprint']} written to {output}")
        return 0
    if args.command == "compare":
        result = compare(json.loads(args.a.read_text(encoding="utf-8")),
                         json.loads(args.b.read_text(encoding="utf-8")))
        print(json.dumps(result, indent=2))
        return 0

    settings = Settings.from_env()
    engine = args.parser or ("tesseract" if args.provider == "ocr" else settings.parser_engine)
    settings = dataclasses.replace(settings, provider=args.provider, parser_engine=engine)
    settings.validate()
    provider, doc_parser, version, retriever = _build(settings, args)
    config_fp = config_fingerprint(build_config(settings, provider, version,
                                                args.rag_k if retriever else 0))
    if args.split == "test":
        _guard_test_run(args, config_fp)
    if args.provider == "vision-api":
        print("NOTE: this run sends every selected page to the configured vendor and is billed.",
              file=sys.stderr)
    report = run_benchmark(provider, settings, args.split, args.data_root, args.limit,
                           doc_parser, version, retriever, args.rag_k)
    output = args.output or Path(f"reports/bench-{args.split}-{args.provider}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    if args.split == "test":
        TEST_RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with TEST_RUN_LOG.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"config_fingerprint": report["config_fingerprint"],
                                  "report": str(output), "at": report["created_at"]}) + "\n")
    print(markdown(report))
    print(f"Report: {output} (and {output.with_suffix('.md')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
