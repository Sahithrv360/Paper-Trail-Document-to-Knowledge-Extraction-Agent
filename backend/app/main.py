"""Start the Paper Trail folder ingestion service."""

import time

from backend.app.config import (DATABASE_PATH, EXTRACTION_DEVICE, EXTRACTION_PROVIDER,
                                GRAPH_PATH, MAX_FILE_BYTES, POLL_INTERVAL_SECONDS,
                                WATCH_FOLDER)
from backend.app.ingestion.registry import DocumentRegistry
from backend.app.ingestion.watcher import FolderWatcher


def run_cycle(watcher, processor=None) -> list:
    """One scan plus a drain of the job queue; without a processor, jobs just queue."""
    watcher.scan_once()
    return processor.process_pending() if processor else []


def build_processor(registry):
    if EXTRACTION_PROVIDER == "none":
        return None
    if EXTRACTION_PROVIDER != "donut":
        raise SystemExit(f"Unsupported EXTRACTION_PROVIDER: {EXTRACTION_PROVIDER!r}")
    from backend.app.extraction.providers import DonutProvider
    from backend.app.graph import GraphStore
    from backend.app.pipeline import JobProcessor
    return JobProcessor(registry, GraphStore(GRAPH_PATH), DonutProvider(EXTRACTION_DEVICE))


def main():
    registry = DocumentRegistry(DATABASE_PATH)
    watcher = FolderWatcher(WATCH_FOLDER, registry, MAX_FILE_BYTES)
    processor = build_processor(registry)
    mode = f"extraction={EXTRACTION_PROVIDER}, graph: {GRAPH_PATH}" if processor else "queue only"
    print(f"Watching {WATCH_FOLDER} (database: {DATABASE_PATH}; {mode})")
    while True:
        for result in run_cycle(watcher, processor):
            print(result)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
