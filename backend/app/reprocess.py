"""Explicitly rerun the configured provider on one already-registered document version.

Uses the same settings as the watcher (``EXTRACTION_PROVIDER``, ``PARSER_ENGINE``, ...).
Reviewed records stay protected: replacing one still requires an explicit decision.
"""

import argparse
import json
from pathlib import Path

from backend.app.config import DATABASE_PATH, GRAPH_PATH, Settings
from backend.app.graph import GraphStore
from backend.app.ingestion.registry import DocumentRegistry
from backend.app.pipeline import JobProcessor, build_parser, build_provider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--document-id")
    target.add_argument("--path", type=Path)
    parser.add_argument("--provider", choices=["donut", "ocr", "vision-api"],
                        help="override EXTRACTION_PROVIDER (default: environment, else donut)")
    args = parser.parse_args()

    settings = Settings.from_env()
    provider_name = args.provider or (settings.provider if settings.provider != "none" else "donut")
    import dataclasses
    settings = dataclasses.replace(
        settings, provider=provider_name,
        parser_engine=(settings.parser_engine if settings.parser_engine != "none" or provider_name != "ocr"
                       else "tesseract"))
    settings.validate()
    registry = DocumentRegistry(DATABASE_PATH)
    document = (registry.get_document(args.document_id) if args.document_id
                else registry.find_document_by_path(args.path))
    if document is None:
        parser.error("document is not registered; start the watcher and let it scan the file first")
    provider = build_provider(settings)
    job_id = registry.enqueue_reprocess(document["document_id"])
    processor = JobProcessor(registry, GraphStore(GRAPH_PATH), provider,
                             parser=build_parser(settings), settings=settings)
    result = next((item for item in processor.process_pending()
                   if item["job_id"] == job_id), None)
    stored = GraphStore(GRAPH_PATH).get_extraction(document["document_id"])
    print(json.dumps({"job": result, "extraction": stored}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
