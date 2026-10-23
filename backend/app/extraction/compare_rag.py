"""Run no-RAG vs RAG on the SAME validation rows and write one auditable report.

Requires local data (data/raw/invoices-donut-data-v1/{train,validation}) and a provider
that actually consumes retrieval context. Donut does not use text prompts, so with
`--provider donut` RAG is a no-op and the report says so; it must not be read as a RAG result.
The test split is deliberately refused: it is reserved for one final run.
"""

import argparse
import json
from pathlib import Path

from .dataset import load_local_split
from .evaluate import evenly_spaced_indices
from .evaluation import aggregate_item_prf, evaluate_examples, evaluate_predictions
from .providers import extract_validated
from .rag import TrainingRetriever


def run(provider, data_root: Path, limit: int, k: int, query_fn, output: Path) -> dict:
    from PIL import Image
    train, _ = load_local_split(data_root, "train")
    val, _ = load_local_split(data_root, "validation")
    rows_sel = [val[i] for i in evenly_spaced_indices(len(val), limit)]
    retriever = TrainingRetriever(train)
    report = {"split": "validation", "provider": provider.name, "revision": provider.revision,
              "k": k, "row_ids": [r["row_index"] for r in rows_sel], "arms": {}}
    for arm in ("no_rag", "rag"):
        rows, failures = [], []
        for src in rows_sel:
            with Image.open(src["image_path"]) as im:
                image = im.convert("RGB")
            context = retriever.retrieve(query_fn(image), k) if arm == "rag" else None
            source = {"split": "validation", "row_index": src["row_index"],
                      "file": src["image_path"].name}
            try:
                out = extract_validated(provider, image, source, context)
            except ValueError as exc:
                failures.append({**source, "error": str(exc)})
                continue
            rows.append({"ground_truth": src["ground_truth"], "prediction": out["record"],
                         "source": source})
        report["arms"][arm] = {"metrics": evaluate_predictions(rows),
                               "item_prf": aggregate_item_prf(rows),
                               "examples": evaluate_examples(rows), "failures": failures,
                               "scored_documents": len(rows)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("donut",), default="donut")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/invoices-donut-data-v1"))
    parser.add_argument("--output", type=Path, default=Path("reports/validation-rag-compare.json"))
    args = parser.parse_args()
    from .providers import DonutProvider
    provider = DonutProvider(args.device)
    print("NOTE: Donut ignores retrieval context; the 'rag' arm is a no-op control, not a RAG result.")
    # No OCR text source is wired yet, so the query is empty (retrieval ordering is then arbitrary).
    report = run(provider, args.data_root, args.limit, args.k, lambda image: "", args.output)
    for arm, data in report["arms"].items():
        print(arm, data["metrics"]["micro_field_accuracy"], data["item_prf"]["f1"])


if __name__ == "__main__":
    main()
