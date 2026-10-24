"""Start the Paper Trail folder ingestion service.

Default: queue-only (no model, no OCR, nothing leaves the device). Set
``EXTRACTION_PROVIDER`` to ``donut``, ``ocr`` or ``vision-api`` to opt in. Scanning runs
in the main thread; one worker thread processes jobs, so a slow document does not
delay detection of new files.
"""

import sys
import threading
import time

from backend.app.config import (DATABASE_PATH, GRAPH_PATH, MAX_FILE_BYTES,
                                POLL_INTERVAL_SECONDS, WATCH_FOLDER, Settings)
from backend.app.ingestion.registry import DocumentRegistry
from backend.app.ingestion.watcher import FolderWatcher


def run_cycle(watcher, processor=None) -> list:
    """One scan plus a drain of the job queue; without a processor, jobs just queue."""
    watcher.scan_once()
    return processor.process_pending() if processor else []


def build_processor(registry, settings: Settings = None):
    settings = settings or Settings.from_env()
    settings.validate()
    if settings.provider == "none":
        return None
    from backend.app.graph import GraphStore
    from backend.app.pipeline import JobProcessor, build_parser, build_provider
    provider = build_provider(settings)
    return JobProcessor(registry, GraphStore(GRAPH_PATH), provider,
                        parser=build_parser(settings), settings=settings)


def remote_notice(settings: Settings) -> str:
    return (f"REMOTE EXTRACTION ENABLED: page images"
            f"{' and OCR text' if settings.vision_send_parser_text and settings.parser_engine != 'none' else ''}"
            f" of each processed document are sent to {settings.vision_api_base_url} "
            f"(model {settings.vision_model}). The vendor bills usage; token counts are "
            "recorded per extraction.")


def worker_loop(processor, stop: threading.Event, wake: threading.Event, interval: float):
    while not stop.is_set():
        for result in processor.process_pending():
            print(result, flush=True)
        wake.wait(interval)
        wake.clear()


def main():
    settings = Settings.from_env()
    try:
        settings.validate()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}")
    registry = DocumentRegistry(DATABASE_PATH)
    recovered = registry.recover_interrupted_jobs()
    if recovered:
        print(f"Recovered {recovered} job(s) interrupted by a previous shutdown.")
    watcher = FolderWatcher(WATCH_FOLDER, registry, MAX_FILE_BYTES)
    try:
        processor = build_processor(registry, settings)
    except Exception as exc:  # e.g. remote extraction requested without opt-in
        raise SystemExit(f"Cannot start extraction: {exc}")
    mode = (f"extraction={settings.provider}, parser={settings.parser_engine}, graph: {GRAPH_PATH}"
            if processor else "queue only")
    print(f"Watching {WATCH_FOLDER} (database: {DATABASE_PATH}; {mode})")
    if settings.provider == "vision-api":
        print(remote_notice(settings), file=sys.stderr)
    stop, wake = threading.Event(), threading.Event()
    worker = None
    if processor:
        worker = threading.Thread(target=worker_loop, name="papertrail-worker",
                                  args=(processor, stop, wake, POLL_INTERVAL_SECONDS),
                                  daemon=True)
        worker.start()
    try:
        while True:
            watcher.scan_once()
            wake.set()
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("Stopping; the current job (if any) finishes or is recovered at next start.")
    finally:
        stop.set()
        wake.set()
        if worker is not None:
            worker.join(timeout=30)


if __name__ == "__main__":
    main()
