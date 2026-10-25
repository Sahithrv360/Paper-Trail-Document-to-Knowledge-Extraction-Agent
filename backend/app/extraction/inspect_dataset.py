"""Inspect dataset splits, annotation labels, and a small example sample."""

import argparse
import json

from .dataset import DATASET_ID, available_splits, fetch_rows
from .schema import unpack_ground_truth


def main():
    try:
        from huggingface_hub import __version__ as hub_version
    except ImportError as exc:
        raise RuntimeError(
            "Install optional extraction dependencies from backend/requirements-extraction.txt"
        ) from exc
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("train", "validation", "test"), default="train")
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    splits = available_splits()
    if args.split not in splits:
        parser.error(f"split {args.split!r} not found; available: {', '.join(splits)}")
    revision, dataset_rows, total = fetch_rows(args.split, max(1, min(args.samples, 100)))
    labels = set()
    examples = []
    for dataset_row in dataset_rows:
        parsed = unpack_ground_truth(dataset_row["row"]["ground_truth"])
        for section, value in parsed.items():
            if isinstance(value, dict):
                labels.update(f"{section}.{key}" for key in value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        labels.update(f"{section}.{key}" for key in item)
            else:
                labels.add(section)
        examples.append(parsed)
    print(json.dumps({
        "dataset": DATASET_ID,
        "config": "default",
        "dataset_revision": revision,
        "available_splits": splits,
        "inspected_split": args.split,
        "sample_count": len(examples),
        "split_rows": total,
        "annotation_labels": sorted(labels),
        "example_shapes": examples,
        "huggingface_hub_version": hub_version,
    }, indent=2))


if __name__ == "__main__":
    main()
