"""Score deterministic "why connected" answers against synthetic golden cases."""

import argparse
import json
from pathlib import Path

from .graph import GraphStore
from .graph_rag import explain_invoice_connections

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = PROJECT_ROOT / "presentation_data" / "golden_chat_questions.json"
DEFAULT_GRAPH = (PROJECT_ROOT / "presentation_data" / "runtime"
                 / "paper_trail_graph.sqlite3")


def evaluate(graph_path=DEFAULT_GRAPH, cases_path=DEFAULT_CASES):
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    if cases.get("version") != 1 or not isinstance(cases.get("cases"), list):
        raise ValueError("golden chat file must have version 1 and a cases array")
    graph = GraphStore(graph_path)
    results = []
    for case in cases["cases"]:
        answer = explain_invoice_connections(
            graph, case["question"], include_unreviewed=case.get("include_unreviewed", False))
        actual_links = sorted(link["relationship"] for link in answer["links"])
        expected_links = sorted(case["expected_links"])
        missing_phrases = [phrase for phrase in case.get("answer_contains", [])
                           if phrase.casefold() not in answer["answer"].casefold()]
        excluded_matches = answer["excluded_unreviewed"] == case.get("excluded_unreviewed", 0)
        passed = actual_links == expected_links and not missing_phrases and excluded_matches
        results.append({"id": case["id"], "passed": passed,
                        "expected_links": expected_links, "actual_links": actual_links,
                        "missing_phrases": missing_phrases,
                        "expected_excluded_unreviewed": case.get("excluded_unreviewed", 0),
                        "actual_excluded_unreviewed": answer["excluded_unreviewed"]})
    passed = sum(result["passed"] for result in results)
    return {"dataset": cases.get("dataset"), "cases": len(results), "passed": passed,
            "pass_rate": passed / len(results) if results else 0.0,
            "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH,
                        help="SQLite graph database to evaluate")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES,
                        help="JSON file containing golden deterministic graph questions")
    args = parser.parse_args(argv)
    print(json.dumps(evaluate(args.graph, args.cases), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
