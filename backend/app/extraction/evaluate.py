"""Run local Donut extraction and compare predictions with invoice annotations."""

import argparse
import json
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from .dataset import DATASET_ID, load_local_split
from .donut import MODEL_ID, MODEL_REVISION, extract_image, load_model
from .evaluation import evaluate_examples, evaluate_predictions
from .schema import normalize_donut_result

def evenly_spaced_indices(row_count: int, limit: int) -> list[int]:
    if row_count <= 0 or limit <= 0:
        return []
    count = min(row_count, limit)
    if count == 1:
        return [0]
    return [round(index * (row_count - 1) / (count - 1)) for index in range(count)]


def _display(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else "null"


def comparison_markdown(report: dict) -> str:
    """Render extracted and expected values next to each other for human review."""
    lines = [
        "# Invoice Extraction Comparison",
        "",
        f"- Dataset split: `{report['split']}`",
        f"- Documents: {report['metrics']['documents_evaluated']}",
        f"- Micro field exact-match accuracy: {report['metrics']['micro_field_accuracy']:.1%}",
        f"- Exact-document accuracy: {report['metrics']['exact_document_accuracy']:.1%}",
        "",
        "Expected values come from the dataset annotations. `Match` scores only fields with an annotation.",
        "",
    ]
    for number, example in enumerate(report["examples"], start=1):
        source = example["source"]
        lines.extend([
            f"## {number}. {source['file']} ({source.get('split', report['split'])} row {source['row_index']})",
            "",
            "| Field | Extracted | Expected annotation | Exact match |",
            "|---|---|---|---|",
        ])
        for field, result in example["fields"].items():
            expected_match = result["exact_match"]
            match_text = "Not scored" if expected_match is None else ("Yes" if expected_match else "No")
            extracted = _display(result["predicted"]).replace("|", "\\|").replace("\n", " ")
            expected = _display(result["expected"]).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{field}` | {extracted} | {expected} | {match_text} |")
        lines.append("")
    return "\n".join(lines)


def run(split: str, limit: int, device: str, output: Path,
        data_root: Path = Path("data/raw/invoices-donut-data-v1")) -> dict:
    try:
        from PIL import Image
        from huggingface_hub import __version__ as hub_version
    except ImportError as exc:
        raise RuntimeError(
            "Install optional extraction dependencies from backend/requirements-extraction.txt"
        ) from exc
    selected_rows, split_dir = load_local_split(data_root, split)
    selected_positions = evenly_spaced_indices(len(selected_rows), limit)
    selected = [selected_rows[position] for position in selected_positions]
    processor, model = load_model(device)
    rows = []
    for source in selected:
        index = source["row_index"]
        with Image.open(source["image_path"]) as opened_image:
            image = opened_image.convert("RGB")
        raw = extract_image(image, processor, model, device)
        prediction = normalize_donut_result(raw, {
            "extraction_method": MODEL_ID,
            "source_split": split,
            "source_index": index,
            "source_file": source["image_path"].name,
            "review_status": "needs_review",
        }).to_dict()
        rows.append({
            "ground_truth": source["ground_truth"],
            "prediction": prediction,
            "source": {
                "dataset": DATASET_ID,
                "split": split,
                "row_index": index,
                "file": source["image_path"].name,
            },
        })
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET_ID,
        "dataset_revision": None,
        "split": split,
        "selected_indices": [row["row_index"] for row in selected],
        "local_split_directory": str(split_dir),
        "model": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "device": device,
        "runtime_versions": {
            "python": sys.version.split()[0],
            "huggingface_hub": hub_version,
            "torch": version("torch"),
            "transformers": version("transformers"),
            "Pillow": version("Pillow"),
        },
        "metrics": evaluate_predictions(rows),
        "limitations": [
            "This is a small baseline, not a production accuracy claim.",
            "The model is fine-tuned on this dataset; the official test split is used, "
            "but split provenance should be independently confirmed before claiming "
            "unbiased generalization.",
            "The dataset combines party name and address and lacks customer phone, "
            "email, and currency labels; these fields are not scored.",
            "Donut outputs do not include field-level evidence snippets or confidence.",
        ],
    }
    report["examples"] = evaluate_examples(rows)
    comparison_path = output.with_suffix(".md")
    report["comparison_report"] = str(comparison_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    comparison_path.write_text(comparison_markdown(report), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("validation", "test"), default="test")
    parser.add_argument("--limit", type=int, default=5,
                        help="Number of evenly spaced local rows; use the split size for all rows.")
    parser.add_argument("--device", default="cpu", help="cpu or an explicit torch device")
    parser.add_argument("--output", type=Path, default=Path("reports/donut-baseline.json"))
    parser.add_argument("--data-root", type=Path,
                        default=Path("data/raw/invoices-donut-data-v1"),
                        help="Root containing train, validation, and test image folders.")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    report = run(args.split, args.limit, args.device, args.output, args.data_root)
    print(json.dumps(report["metrics"], indent=2))
    print(f"Full report written to {args.output}")
    print(f"Side-by-side comparison written to {report['comparison_report']}")


if __name__ == "__main__":
    main()
