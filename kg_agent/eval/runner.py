"""Evaluate retrieval modes against a small labeled sample question set."""

from pathlib import Path
import re
import time

import yaml


def _tokens(text):
    normalized = re.sub(r"(?<=\d),(?=\d)", "", text.casefold())
    return {token[:-1] if token.endswith("s") and len(token) > 4 else token
            for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 2}


def load_dataset(path: Path | str):
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("Evaluation YAML must have a cases list")
    if len(payload["cases"]) < 20:
        raise ValueError("Evaluation dataset needs at least 20 cases")
    return payload["cases"]


def evaluate(agent, cases, modes=("dense", "graph", "keyword", "hybrid", "agent")):
    results = []
    for mode in modes:
        recalls, correctness, faithfulness, latencies, calls = [], [], [], [], []
        case_results = []
        for case in cases:
            before_calls = getattr(agent.llm, "call_count", len(getattr(agent.llm, "calls", [])))
            started = time.perf_counter()
            response = agent.run(case["question"], mode=mode)
            latencies.append((time.perf_counter() - started) * 1000)
            after_calls = getattr(agent.llm, "call_count", len(getattr(agent.llm, "calls", [])))
            calls.append(after_calls - before_calls)
            retrieved = {value for item in response.evidence for value in
                          [item.id, *item.node_ids, *item.edge_ids, *item.chunk_ids]}
            gold = set(case["gold_evidence_ids"])
            recalls.append(len(gold & retrieved) / max(1, len(gold)))
            expected = _tokens(case["gold_answer"])
            actual = _tokens(response.answer)
            correctness.append(len(expected & actual) / max(1, len(expected)))
            faithfulness.append(1.0 if response.citations and all(
                citation.startswith(("C", "T", "P", "S", "N"))
                for citation in response.citations) else 0.0)
            case_results.append({"intent": case.get("intent", "unspecified"),
                "recall": recalls[-1], "correctness": correctness[-1],
                "faithfulness": faithfulness[-1]})
        by_intent = {}
        for intent in sorted({item["intent"] for item in case_results}):
            group = [item for item in case_results if item["intent"] == intent]
            by_intent[intent] = {key: sum(item[key] for item in group) / len(group)
                                 for key in ("recall", "correctness", "faithfulness")}
        results.append({"mode": mode, "recall_at_k": sum(recalls) / len(recalls),
            "answer_correctness": sum(correctness) / len(correctness),
            "faithfulness": sum(faithfulness) / len(faithfulness),
            "latency_ms": sum(latencies) / len(latencies),
            "llm_calls": sum(calls) / max(1, len(calls)),
            "by_intent": by_intent,
            "availability": ("not configured: build the dense index first"
                             if mode == "dense" and agent.dense_index is None else "ready")})
    return results


def render_report(results):
    lines = ["# KG Agent evaluation", "",
        "Scores use the checked-in synthetic sample graph. Answer correctness is token overlap "
        "with labeled answer terms; faithfulness measures citation-ID validity, not semantic "
        "claim entailment. These are development metrics, not production accuracy.", "",
        "| Mode | Recall@k | Answer correctness | Citation faithfulness | Latency (ms) | LLM calls |",
        "|---|---:|---:|---:|---:|---:|"]
    for row in results:
        lines.append(f"| {row['mode']} | {row['recall_at_k']:.3f} | "
                     f"{row['answer_correctness']:.3f} | {row['faithfulness']:.3f} | "
                     f"{row['latency_ms']:.1f} | {row['llm_calls']:.2f} |")
        if row.get("availability") != "ready":
            lines.append(f"\nDense mode status: {row['availability']}. Its zero scores are not a model comparison.")
    lines.append("")
    intents = sorted({intent for row in results for intent in row.get("by_intent", {})})
    if intents:
        lines.extend(["## By intent", "", "| Mode | Intent | Recall@k | Answer correctness | Citation faithfulness |",
                      "|---|---|---:|---:|---:|"])
        for row in results:
            for intent, metrics in row.get("by_intent", {}).items():
                lines.append(f"| {row['mode']} | {intent} | {metrics['recall']:.3f} | "
                             f"{metrics['correctness']:.3f} | {metrics['faithfulness']:.3f} |")
        lines.append("")
    return "\n".join(lines)
