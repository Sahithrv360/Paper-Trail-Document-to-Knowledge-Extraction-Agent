# Codex Handoff — Paper Trail

This file is a working brief for the next coding agent. Read it with `AGENTS.md`, `APPROACH.md`, `docs/build-plan.md`, `docs/SDD.md`, and `README.md` before changing the project.

## Goal

Build an inspectable document-to-knowledge prototype for procurement invoices and contracts. Every accepted fact should be traceable to its source. Keep uncertain facts and entity links unresolved for human review. This is a research prototype, not a production system.

## Current state

- `backend/app/ingestion/` watches a local folder, tracks versions and jobs, and supports retries/deletions.
- `backend/app/extraction/` contains the canonical invoice schema, pretrained Donut adapter, evaluation helpers, and a standalone PaddleOCR preview command.
- `backend/app/pipeline.py` connects supported JPG/PNG jobs to extraction and SQLite graph sync. Results stay `needs_review`.
- `backend/app/graph.py` stores document-supported entities/facts in SQLite and can export graph JSON.
- No frontend, review API, PDF extraction path, OCR-to-field mapping, live master-data connector, or contract extraction pipeline exists.
- The small recorded Donut test baseline is 28/75 exact fields and 0/5 exact documents. Treat this as a limited baseline only.
- `docs/prototype.html` is a static UX sketch. It is not connected to the backend.

## Prioritized work

1. **Evidence-capable document extraction:** add page-aware PDF text extraction and OCR fallback; map recognized content to the canonical schema and preserve source locations for each fact. The current PaddleOCR CLI exposes raw OCR/layout output only; do not describe it as invoice extraction.
2. **Labelled validation:** create or curate permitted examples for fields, evidence spans, OCR errors, ambiguous vendors, missing/conflicting data, and conditional contract language. Keep development/validation/test splits separate. Compare on validation data before changing models; reserve test data for final reporting.
3. **Conservative entity linking:** consume an approved master-record source; produce candidate reasons and abstain when evidence is ambiguous. Never invent identifiers or silently link similar names.
4. **Reviewer surface:** provide a queue and document detail view with extracted values, source evidence, warnings, candidate matches, and status. Add review decisions only with auditable persistence and protection for approved/corrected records.
5. **Operational reliability:** consider stale processing-job recovery and clear failure reporting. Keep processing idempotent and prevent failed extraction from corrupting the last valid graph state.

Follow the detailed sequence and exit evidence in [`docs/build-plan.md`](docs/build-plan.md). Do not skip directly to hosted models or enterprise deployment before privacy constraints and labelled performance are established.

## Non-negotiable project rules

- Reuse the existing watcher, schema, Donut baseline, SQLite graph, and evaluator where they fit. Do not rebuild a warehouse.
- Keep extracted mentions distinct from resolved master entities.
- Require source evidence for claims in the target workflow; schema validity or model confidence is not evidence.
- Keep all model-derived results reviewable; never auto-approve them.
- Use synthetic documents during early development. Do not send confidential documents to external services without approval.
- Do not commit credentials, confidential documents, downloaded datasets, or local evaluation reports.
- Do not claim accuracy improvements without reproducible labelled evaluation. State sample size and whether a run is mocked or real inference.
- Keep `APPROACH.md`, `docs/build-plan.md`, `docs/SDD.md`, `README.md`, and `artefact.md` consistent with actual implementation.

## Useful commands

Run from the repository root in PowerShell:

```powershell
python -m unittest discover -s backend/tests -v
python -m backend.app.main
```

The watcher is queue-only by default. For local Donut extraction, install `backend/requirements-extraction.txt` and set `EXTRACTION_PROVIDER=donut`. The first inference downloads a large checkpoint. Dataset tools expect local data under the ignored `data/raw/` tree; never place evaluation documents in `watched/`.

Optional PaddleOCR preview, not connected to the invoice pipeline:

```powershell
python -m pip install -r backend/requirements-paddleocr.txt
python -m backend.app.extraction.paddle_ocr --image backend/tests/fixtures/invoice-242.jpg --device cpu
```

## Working method

Inspect existing modules and docs before editing. Keep changes focused and update documentation when behavior changes. Preserve the current safe defaults and compatibility. Use synthetic fixtures for tests. Report what changed, validation performed, limitations, and the next concrete milestone in the handoff.
