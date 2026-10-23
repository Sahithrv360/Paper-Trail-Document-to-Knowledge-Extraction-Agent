"""Configuration for the folder ingestion service."""

import os
from pathlib import Path


WATCH_FOLDER = Path(os.getenv("WATCH_FOLDER", "./watched")).expanduser().resolve()
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "./paper_trail.sqlite3")).expanduser().resolve()
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "2"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_MB", "25")) * 1024 * 1024
SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}
GRAPH_PATH = Path(os.getenv("GRAPH_PATH", "./paper_trail_graph.sqlite3")).expanduser().resolve()
# "none" keeps the original queue-only behaviour; "donut" opts in to local Donut extraction.
EXTRACTION_PROVIDER = os.getenv("EXTRACTION_PROVIDER", "none").lower()
EXTRACTION_DEVICE = os.getenv("EXTRACTION_DEVICE", "cpu")
