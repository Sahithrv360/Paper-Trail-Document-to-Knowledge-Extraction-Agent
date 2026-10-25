# Paper Trail Implementation Guide

This guide describes the current prototype, its limits, and the commands for running it. The repository is a local research prototype, not a production invoice system.

## Components

- `backend/app/ingestion/` watches a configured folder, waits for files to stabilize, tracks versions and queues work in SQLite.
- `backend/app/extraction/` contains the canonical invoice schema, Donut adapter, evidence matching, structured validation, dataset tools, and evaluation commands.
- `backend/app/extraction/paddle_ocr.py` is an optional PP-StructureV3 preview CLI. It emits raw OCR text, line scores and coordinates, tables, and layout blocks; it does not create canonical invoice fields or update the graph.
- `backend/app/extraction/document_input.py`, `local_ocr.py`, and `ocr_mapping.py` provide bounded image/PDF input, Tesseract or optional RapidOCR parsing, and evidence-linked field mapping. `vision_api.py` is disabled unless all explicit remote opt-in settings are provided.
- `backend/app/pipeline.py` connects queued image jobs to extraction and graph persistence. `backend/app/ledger.py` stores versioned extractions, review history, source claims, and graph changes; `backend/app/graph.py` exposes the graph API and JSON export. The graph is rebuilt deterministically from active claims.
- `backend/app/review/` serves the loopback-only local review page; `backend/app/extraction/benchmark.py` provides validation and frozen test-split evaluation.
- `backend/app/graph_ontology.py` defines the allowed node/edge types and directions. `docs/DATA_CONTRACTS.md` documents the extraction, evidence, review, and ledger shapes.
- `backend/tests/` contains unit tests and a synthetic invoice fixture. The fixture tests use replayed/mock provider output; they do not establish model accuracy.
- `docs/SDD.md` records the architecture, implementation status, and known limitations. `data/README.md` documents the public dataset and its local layout.

## Run the watcher

From the repository root, run the tests and start the watcher:

```powershell
python -m unittest discover -s backend/tests -v
python -m backend.app.main
```

The default mode scans `./watched`, records documents/jobs in `./paper_trail.sqlite3`, and queues files without extracting them. `WATCH_FOLDER`, `DATABASE_PATH`, `POLL_INTERVAL_SECONDS`, and `MAX_FILE_MB` configure the watcher. The graph database uses SQLite WAL mode and upgrades the previous graph schema to version 2 while retaining legacy tables for audit.

To enable local Donut extraction and graph synchronization, install the optional dependencies and set the provider before starting:

```powershell
python -m pip install -r backend/requirements-extraction.txt
$env:EXTRACTION_PROVIDER = 'donut'
python -m backend.app.main
```

The Donut checkpoint downloads on first use. Every extraction stays `needs_review` until a person reviews it. Evidence and arithmetic findings flag unsupported or inconsistent fields without changing printed values. A changed document creates a new extraction version; reviewed versions are protected unless replacement is explicitly enabled by the caller. To reprocess a registered image, use `python -m backend.app.reprocess --path watched/invoice-242.jpg`.

## Lightweight OCR and PDF input

Install the Tesseract binary separately, then install the optional Python packages. Tesseract runs as a one-thread subprocess with a timeout. For PDFs, install pypdfium2; digital text is read directly and scanned pages are rendered under configured page/pixel limits.

```powershell
python -m pip install -r backend/requirements-ocr.txt -r backend/requirements-pdf.txt
python -m backend.app.extract_one backend/tests/fixtures/invoice-242.jpg --provider ocr
```

RapidOCR is optional and uses a separate process, but the milestone measurements found substantially higher memory use than Tesseract. Avoid selecting it on a constrained laptop until its resource use is acceptable.

## Local review and evaluation

Run `python -m backend.app.review.server` to start the loopback-only review page. Keep the printed one-time link private. To preview the hosted provider without sending a document, use `python -m backend.app.extract_one <file> --provider vision-api --dry-run`; actual sending remains disabled unless the explicit acceptance phrase, API key, and model are configured.

Use `python -m backend.app.extraction.benchmark run --split validation --provider ocr --parser tesseract` after downloading the labeled dataset. Compare providers on identical validation rows. Freeze one configuration before the guarded test run; see `docs/EVALUATION.md`.

## PaddleOCR preview

This standalone path is for inspecting OCR output before building and measuring invoice-field mapping. It is separate from the watcher and Donut provider. Install its optional dependencies and run it on an image:

```powershell
python -m pip install -r backend/requirements-paddleocr.txt
python -m backend.app.extraction.paddle_ocr --image backend/tests/fixtures/invoice-242.jpg --device cpu
```

The first run downloads PaddleOCR models. The command writes JSON under `reports/paddleocr/` by default and prints recognized text. Use `--output` to choose another report path. CPU mode disables MKL-DNN because Paddle's Windows oneDNN inference path can fail on some model attributes. It provides no accuracy result; later evaluation should compare mapped fields against labelled validation data.

## Dataset evaluation

Download the Hugging Face dataset into the ignored `data/raw/invoices-donut-data-v1/` layout described in `data/README.md`. Install the extraction requirements, then inspect or evaluate a held-out split:

```powershell
python -m backend.app.extraction.inspect_dataset --split train --samples 3
python -m backend.app.extraction.evaluate --split test --limit 5 --device cpu
```

Reports are written under ignored `reports/`. The historical five-document Donut baseline scored 28/75 exact fields (37.3%) and 0/5 exact documents. The dataset benchmark has not been run with the new OCR or API paths, so no accuracy gain is established. See `docs/EVALUATION.md` for the validation-first procedure.
