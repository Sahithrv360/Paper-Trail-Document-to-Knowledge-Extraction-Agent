"""Extract one invoice file and print the validated record as JSON.

Preview tool: it does not touch the watcher registry or the graph. Local providers
(``ocr``, ``donut``) never send data anywhere. ``vision-api`` sends the page images
(and OCR text, if a parser is selected) to the configured vendor only when every
opt-in condition is met; use ``--dry-run`` to see what would be sent, unsent.

Examples::

    python -m backend.app.extract_one invoice.jpg --provider ocr
    python -m backend.app.extract_one invoice.pdf --provider ocr --parser rapidocr
    python -m backend.app.extract_one invoice.jpg --provider vision-api --dry-run
"""

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

from backend.app.config import Settings


def _notice(settings, document, size_bytes=None) -> str:
    if settings.provider != "vision-api":
        return f"Local extraction ({settings.provider}); nothing leaves this device."
    size = f", about {size_bytes / 1024:.0f} KB" if size_bytes else ""
    text = " and local OCR text" if settings.vision_send_parser_text and settings.parser_engine != "none" else ""
    return (f"PRIVACY/COST NOTICE: {len(document.pages)} page image(s){text}{size} would be sent "
            f"to {settings.vision_api_base_url} for model {settings.vision_model or '<unset>'}. "
            "The vendor bills by token; usage is reported in provenance.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument("--provider", choices=["ocr", "donut", "vision-api"], required=True)
    parser.add_argument("--parser", choices=["none", "tesseract", "rapidocr"],
                        help="OCR engine for evidence text (default: tesseract for ocr, none otherwise)")
    parser.add_argument("--dry-run", action="store_true",
                        help="vision-api only: build and describe the request without sending it")
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    engine = args.parser or ("tesseract" if args.provider == "ocr" else settings.parser_engine)
    settings = dataclasses.replace(settings, provider=args.provider, parser_engine=engine)
    try:
        settings.validate()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 3

    from backend.app.extraction.document_input import DocumentLoadError, load_document
    from backend.app.extraction.providers import InvalidModelResponse, extract_validated
    from backend.app.pipeline import build_parser, build_provider

    try:
        document = load_document(args.path, settings.max_pages, settings.max_page_pixels,
                                 settings.pdf_render_dpi)
    except DocumentLoadError as exc:
        print(f"Cannot read document: {exc}", file=sys.stderr)
        return 3
    started = time.perf_counter()
    parse = build_parser(settings)(document)
    parse_seconds = time.perf_counter() - started

    if args.provider == "vision-api" and args.dry_run:
        from backend.app.extraction.vision_api import build_request_body
        body = build_request_body(settings, document, None, parse)
        size = len(json.dumps(body).encode("utf-8"))
        print(_notice(settings, document, size), file=sys.stderr)
        blockers = settings.remote_blockers()
        summary = {"dry_run": True, "would_send_bytes": size, "pages": len(document.pages),
                   "parser_text_chars": len(parse.full_text()) if parse else 0,
                   "within_size_limit": size <= settings.vision_max_request_bytes,
                   "remote_blockers": blockers}
        print(json.dumps(summary, indent=2))
        return 0

    print(_notice(settings, document), file=sys.stderr)
    try:
        provider = build_provider(settings)
    except Exception as exc:
        print(f"Cannot start provider: {exc}", file=sys.stderr)
        return 3
    started = time.perf_counter()
    try:
        result = extract_validated(provider, document, {"source_file": args.path.name},
                                   parse=parse)
    except InvalidModelResponse as exc:
        print(f"Invalid model response: {exc}", file=sys.stderr)
        return 2
    output = {"validation_status": result["validation_status"],
              "review_status": result["record"]["review_status"],
              "record": result["record"], "findings": result["findings"],
              "timing_seconds": {"parse": round(parse_seconds, 3),
                                 "extract": round(time.perf_counter() - started, 3)}}
    text = json.dumps(output, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote {args.output} ({result['validation_status']})", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
