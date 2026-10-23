# Repository Guidelines

## Project Structure & Module Organization

The Python backend lives in `backend/app/`: `ingestion/` handles folder watching and job state, `extraction/` contains Donut, schema, dataset, and evaluation code, and `graph.py` persists document-supported facts in SQLite. Tests and the synthetic invoice fixture are in `backend/tests/`. `docs/` contains the functional requirements, software design document, and implementation guide; `data/README.md` documents the public invoice dataset. `assests/` holds the build prompt and architecture asset. Keep downloaded evaluation data separate from the live `watched/` folder.

## Build, Test, and Development Commands

Run `python -m unittest discover -s backend/tests -v` from the repository root. Start queue-only watching with `python -m backend.app.main`; set `EXTRACTION_PROVIDER=donut` to opt into local Donut inference and graph sync. Install extraction dependencies with `python -m pip install -r backend/requirements-extraction.txt`; this includes PyTorch and downloads a large model at first inference. Configure paths and limits with `WATCH_FOLDER`, `DATABASE_PATH`, `GRAPH_PATH`, `POLL_INTERVAL_SECONDS`, and `MAX_FILE_MB`.

## Coding Style & Naming Conventions

Use four spaces for Python indentation, descriptive `snake_case` module and function names, and `CapWords` class names. Keep source under `backend/app/`; use `test_*.py` names for standard-library unittest tests. No formatter or linter is configured. For Markdown, use clear heading levels, concise prose, and fenced code blocks for commands.

## Testing Guidelines

Run the full suite with `python -m unittest discover -s backend/tests -v`. Tests use `unittest`; image and model evaluation additionally needs the optional extraction dependencies and local dataset. Keep test fixtures synthetic or appropriately licensed. Do not report evaluation results without reproducible runs on labelled data, and distinguish mocked-provider tests from real model inference.

## Commit & Pull Request Guidelines

Recent commits use short, informal subjects such as `revised functional.md` and `updated .gitignore`; no stricter convention is established. Use concise imperative subjects that identify the change (for example, `Document evaluation workflow`). Pull requests should describe the purpose and affected files, link relevant project requirements when applicable, and include screenshots for meaningful diagram or document changes. Note validation performed and any outstanding limitations.

## Security & Data Handling

Do not commit credentials, confidential documents, or local evaluation artifacts. Use synthetic documents during early development, keep secrets in ignored `.env` files, and document dataset provenance and licences. Do not send sensitive documents to external services without appropriate approval.
