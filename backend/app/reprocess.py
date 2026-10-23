"""Explicitly rerun Donut on one already-registered document version."""

import argparse
import json
from pathlib import Path

from backend.app.config import DATABASE_PATH, EXTRACTION_DEVICE, GRAPH_PATH
from backend.app.extraction.providers import DonutProvider
from backend.app.graph import GraphStore
from backend.app.ingestion.registry import DocumentRegistry
from backend.app.pipeline import JobProcessor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--document-id")
    target.add_argument("--path", type=Path)
    args = parser.parse_args()

    registry = DocumentRegistry(DATABASE_PATH)
    document = (registry.get_document(args.document_id) if args.document_id
                else registry.find_document_by_path(args.path))
    if document is None:
        parser.error("document is not registered; start the watcher and let it scan the file first")
    provider = DonutProvider(EXTRACTION_DEVICE)
    job_id = registry.enqueue_reprocess(document["document_id"])
    processor = JobProcessor(registry, GraphStore(GRAPH_PATH), provider)
    result = next((item for item in processor.process_pending()
                   if item["job_id"] == job_id), None)
    stored = GraphStore(GRAPH_PATH).get_extraction(document["document_id"])
    print(json.dumps({"job": result, "extraction": stored}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
