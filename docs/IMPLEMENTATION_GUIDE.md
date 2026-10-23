# Paper Trail Implementation Guide

This guide describes the current prototype, its limits, and the commands for running it. The repository is a local research prototype, not a production invoice system.

## Components

- `backend/app/ingestion/` watches a configured folder, waits for files to stabilize, tracks versions and queues work in SQLite.
- `backend/app/extraction/` contains the canonical invoice schema, the pinned Donut adapter, dataset tools, and evaluation/comparison commands.
- `backend/app/pipeline.py` connects queued image jobs to extraction and graph persistence. `backend/app/graph.py` stores document-supported invoice, party, and line-item facts in SQLite and exports graph JSON.
- `backend/tests/` contains unit tests and a synthetic invoice fixture. The fixture tests use replayed/mock provider output; they do not establish model accuracy.
- `docs/SDD.md` records the architecture, implementation status, and known limitations. `data/README.md` documents the public dataset and its local layout.

## Run the watcher

From the repository root, run the tests and start the watcher:

```powershell
python -m unittest discover -s backend/tests -v
python -m backend.app.main
```

The default mode scans `./watched`, records documents/jobs in `./paper_trail.sqlite3`, and queues files without extracting them. `WATCH_FOLDER`, `DATABASE_PATH`, `POLL_INTERVAL_SECONDS`, and `MAX_FILE_MB` configure the watcher.

To enable local Donut extraction and graph synchronization, install the optional dependencies and set the provider before starting:

```powershell
python -m pip install -r backend/requirements-extraction.txt
$env:EXTRACTION_PROVIDER = 'donut'
python -m backend.app.main
```

The model checkpoint downloads on first use. JPG and PNG processing is implemented; PDF extraction/OCR is not. Extraction results stay in `needs_review`. To explicitly reprocess a registered image, use `python -m backend.app.reprocess --path watched/invoice-242.jpg`.

## Dataset evaluation

Download the Hugging Face dataset into the ignored `data/raw/invoices-donut-data-v1/` layout described in `data/README.md`. Install the extraction requirements, then inspect or evaluate a held-out split:

```powershell
python -m backend.app.extraction.inspect_dataset --split train --samples 3
python -m backend.app.extraction.evaluate --split test --limit 5 --device cpu
```

Reports are written under ignored `reports/`. The recorded five-document baseline scored 28/75 field values (37.3%) and 0/5 exact documents. This small result does not show generalization or improvement. Donut has no source-span confidence/evidence, RAG is not connected to the live pipeline, and there is no review API or frontend.
