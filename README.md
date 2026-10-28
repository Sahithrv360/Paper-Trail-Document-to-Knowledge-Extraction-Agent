# Paper Trail — Document-to-Knowledge Extraction Agent

Paper Trail is a capstone project focused on extracting structured, evidence-backed knowledge from enterprise documents such as invoices and contracts.

Instead of treating OCR or PDF text extraction as the final result, the system aims to identify entities, relationships, and business facts, connect them to existing master records only when supported by evidence, and flag ambiguous cases for human review.

> **Project status:** The local pipeline supports bounded image/PDF input, Tesseract or RapidOCR extraction, Donut, and an explicitly opt-in vision API; it validates evidence, stores versioned records in a SQLite ledger, syncs a deterministic graph, and provides a local review page and benchmark harness. The 147-test suite passes here (2 optional Tesseract tests skipped); dataset accuracy has not been evaluated. See [`docs/MILESTONES_REPORT.md`](./docs/MILESTONES_REPORT.md) for scope and limits.

## Problem Statement

Enterprise documents contain important business information in unstructured formats. Extracting text alone does not determine whether a claim is supported, which master record an entity represents, or whether a contract clause describes a completed event or a conditional possibility.

Paper Trail aims to address these challenges through evidence provenance, structured validation, conservative entity resolution, and explicit uncertainty handling.

## Objectives

- Process digital and scanned PDF documents.
- Extract entities, relationships, and business facts.
- Attach source evidence and page references where available.
- Compare extracted entities with existing master records.
- Leave ambiguous links unresolved and route them for review.
- Preserve conditional and uncertain language.
- Evaluate accuracy, reliability, latency, and cost.

## Proposed Architecture

The planned processing pipeline is:

1. Document upload and validation.
2. PDF text extraction and OCR fallback.
3. Text preprocessing and source-location preservation.
4. Entity, relationship, and fact extraction.
5. Structured-output validation.
6. Evidence-support validation.
7. Master-record candidate generation and entity resolution.
8. Human review for uncertain cases.
9. Structured result storage and presentation.
10. Evaluation and observability.

See [`architechture.png`](./architechture.png) for the initial architecture image. The diagram should be reviewed and updated to reflect the implemented system.

## Planned Technology Stack

The technology stack remains subject to capstone constraints and testing.

| Component | Proposed option |
|---|---|
| Application language | Python |
| API | FastAPI |
| PDF processing | A PDF text-extraction library |
| OCR | An OCR engine suitable for the selected environment |
| Structured validation | Pydantic or JSON Schema |
| Initial metadata storage | SQLite |
| Knowledge extraction | Replaceable LLM interface |
| Evaluation | Versioned labelled examples and a repeatable evaluation runner |
| User interface | A lightweight document-review interface |

These are proposed choices, not a statement that the components are already implemented.

## Repository Structure

The planned structure is:

```text
Paper-Trail-Document-to-Knowledge-Extraction-Agent/
├── README.md
├── APPROACH.md
├── ARTEFACT.md
├── architechture.png
├── docs/
│   └── functional.md
├── src/
│   └── paper_trail/
├── prompts/
├── data/
│   ├── synthetic/
│   └── evaluation/
├── tests/
├── evals/
├── scripts/
├── .env.example
├── .gitignore
└── requirements.txt
```

This is the intended structure; directories and files should be added as they become necessary. Keep held-out evaluation data separate from development data.

## Getting Started

### Prerequisites

- Python version compatible with the selected dependencies.
- Git.
- An OCR engine if required by the chosen OCR integration.
- Model-provider credentials only if an approved hosted model is selected.

### Setup

Clone the repository:

```bash
git clone https://github.com/Sahithrv360/Paper-Trail-Document-to-Knowledge-Extraction-Agent.git
cd Paper-Trail-Document-to-Knowledge-Extraction-Agent
```

Create and activate a virtual environment.

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

The first implementation slice is a Python folder ingester using only the standard library. No dependency installation is required. From the repository root, run its tests with:

```bash
python -m unittest discover -s backend/tests -v
```

Run the watcher with:

```powershell
python -m backend.app.main
```

It watches `./watched` and stores its document registry in `./paper_trail.sqlite3` by default. Set `WATCH_FOLDER` and `DATABASE_PATH` to change those paths; `POLL_INTERVAL_SECONDS` defaults to `2`, and `MAX_FILE_MB` defaults to `25`. By default it only queues files. In PowerShell, set `$env:EXTRACTION_PROVIDER = 'donut'` to enable local Donut extraction and graph sync. JPG/PNG are processed; PDFs are queued and then fail because OCR/PDF extraction is not implemented. All records remain `needs_review`.

To explicitly rerun a registered image with the current model/schema, use `python -m backend.app.reprocess --path watched/invoice-242.jpg`. The command prints the stored extraction and protects records marked `approved` or `corrected`.

### Invoice Extraction Baseline

The dataset and field mapping are documented in [`data/README.md`](./data/README.md). Install the optional model libraries only when running extraction; this downloads an approximately 809 MB model checkpoint into the Hugging Face cache. The evaluator reads the downloaded images and annotations from `data/raw/invoices-donut-data-v1/`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-extraction.txt
python -m backend.app.extraction.inspect_dataset --split train --samples 3
python -m backend.app.extraction.evaluate --split test --limit 5 --device cpu
```

The evaluator selects deterministic, evenly spaced local rows and writes aggregate metrics plus per-file field comparisons to `reports/donut-baseline.json` and `reports/donut-baseline.md`. Run `--limit 26` to evaluate all test images. Use validation data when experimenting with improvements and keep test data for final evaluation. Do not copy dataset invoices into `watched/`. The model does not provide field evidence or confidence, and dataset party labels combine names with addresses; unavailable fields remain null and results require review.

### PaddleOCR Text and Table Preview

PaddleOCR is an optional OCR path for inspecting a local invoice image. It writes recognized lines, recognizer scores, geometry, table output, and layout blocks to JSON. It does not map text to invoice fields, update the graph, or produce an accuracy score. Install it separately from Donut:

```powershell
python -m pip install -r backend/requirements-paddleocr.txt
python -m backend.app.extraction.paddle_ocr --image backend/tests/fixtures/invoice-242.jpg --device cpu
```

The first run downloads PaddleOCR models. The default report is `reports/paddleocr/invoice-242.json` (reports are git-ignored). CPU mode disables MKL-DNN to avoid a known class of Windows oneDNN inference failures. OCR recognition scores are not invoice-field confidence. PP-StructureV3 is not used by the pipeline; the lighter Tesseract/RapidOCR paths should be used for mapped extraction.

### Local OCR, PDF, review, and benchmark

For the bounded OCR pipeline, install Tesseract separately, then install optional Python requirements. The PDF extra enables digital text extraction and scanned-page rendering:

```powershell
python -m pip install -r backend/requirements-ocr.txt -r backend/requirements-pdf.txt
python -m backend.app.extract_one backend/tests/fixtures/invoice-242.jpg --provider ocr
python -m backend.app.review.server
```

The review server binds to `127.0.0.1`; keep its one-time link private. Copy `.env.example` to `.env` to configure limits and providers. Queue-only remains the default. For labeled evaluation, use `backend.app.extraction.benchmark` and the validation split first; reserve the test split for a frozen, one-time final run. See [`docs/EVALUATION.md`](./docs/EVALUATION.md) and [`docs/DATA_HANDLING.md`](./docs/DATA_HANDLING.md). No accuracy improvement is established until that evaluation runs.

### Neo4j graph view and Graph RAG

SQLite remains the source of truth. Neo4j receives a rebuildable graph projection. The sync also creates direct `SAME_VENDOR_AS`, `SAME_CUSTOMER_AS`, and `SAME_ITEM_DESCRIPTION_AS` invoice links when two invoices share a party node or an exact normalized item description. These links identify the shared basis; an item-description match does not claim the products are identical. No SQLite migration is needed: rerun sync after importing or reviewing invoices.

For the local Docker database, check that the container is running and its ports are published (`7474` for Browser and `7687` for Bolt). If `.env` does not exist, copy `.env.example` to `.env`; then set `NEO4J_URI=bolt://127.0.0.1:7687`, `NEO4J_USERNAME=neo4j`, and `NEO4J_PASSWORD` to the password you set for the local container. Keep `.env` private. Install the optional driver and import the SQLite graph:

```powershell
python -m pip install -r backend/requirements-neo4j.txt
python -m backend.app.neo4j_sync
```

The sync is repeatable and imports only the application's known node and relationship types. Add `--reviewed-only` to project only approved/corrected facts. Open Neo4j Browser at `http://localhost:7474` and try `MATCH (n:PaperTrail) RETURN n LIMIT 100`.

To inspect invoice-to-invoice links in Neo4j Browser, run `MATCH (a:PaperTrail:Invoice)-[r]->(b:PaperTrail:Invoice) RETURN a,r,b`. To rebuild after new extractions, reviews, or corrections, run `python -m backend.app.neo4j_sync` again. In the Paper Trail Graph view, use **Sync SQLite graph to Neo4j** after starting the review server; select an invoice to see its vendor, customer, line items, support, and shared-fact links below the overview.

Start the combined graph and chat review UI with:

```powershell
python -m backend.app.review.server
```

The Graph view can sync the SQLite graph to Neo4j and displays the full invoice relationship shape. Graph RAG uses bounded, read-only retrieval from the Neo4j projection. First inspect retrieved context without making an API call:

```powershell
python -m backend.app.graph_rag "Which invoices were issued by Acme Books?"
```

To ask the configured Anthropic model to summarize that context, configure `GRAPH_RAG_API_KEY` and `GRAPH_RAG_MODEL`, then explicitly set `PAPERTRAIL_ALLOW_GRAPH_RAG_REMOTE=I_ACCEPT_GRAPH_DATA_LEAVES_THIS_DEVICE` and pass `--generate`. The LLM cannot write to the graph; answers include the source invoice/document records returned by retrieval. The remote call is off by default.

## Example Expected Behaviour

### Ambiguous Entity Linking

If an invoice names "Sri Venkateswara Traders Pvt Ltd" and the vendor master contains similar names, the system should show candidate matches and their supporting evidence. It should leave the link unresolved if the available evidence is insufficient.

### Conditional Contract Clause

If a contract states that it "may be renewed for a further term by mutual consent of the parties", the system should extract a conditional renewal clause. It should not assert that renewal has occurred or is guaranteed.

These are required target behaviours. Their correctness must be verified through tests.

## Evaluation

The planned evaluation covers:

- Entity and relationship precision, recall, and F1.
- Evidence-support accuracy.
- Entity-linking precision and candidate ranking.
- Conditional-obligation interpretation.
- Schema validity and correct abstention.
- Latency, cost, failure rate, and reviewer corrections.

Evaluation results will be added only after repeatable runs against labelled data.

## Privacy and Security

- Use synthetic documents during initial development.
- Do not commit API keys or secrets.
- Do not send sensitive documents to external services without appropriate approval.
- Avoid logging raw document contents unless specifically justified and approved.
- Document dataset provenance and licences.
- Keep confidential documents and local evaluation artifacts out of the public repository.

## Documentation

- [`docs/functional.md`](./docs/functional.md) — requirements and acceptance criteria.
- [`docs/DATA_CONTRACTS.md`](./docs/DATA_CONTRACTS.md) — extraction, evidence, ledger, and graph contracts.
- [`docs/DATA_HANDLING.md`](./docs/DATA_HANDLING.md) — local/remote processing and privacy controls.
- [`docs/EVALUATION.md`](./docs/EVALUATION.md) — benchmark workflow and metric definitions.
- [`docs/MILESTONES_REPORT.md`](./docs/MILESTONES_REPORT.md) — delivered milestones and unrun evaluation work.
- [`APPROACH.md`](./APPROACH.md) — target users, build-versus-reuse decisions, architecture, UX sketch, and approach.
- [`docs/build-plan.md`](./docs/build-plan.md) — phased implementation sequence and exit evidence.
- [`docs/prototype.html`](./docs/prototype.html) — static reviewer-flow UX prototype.
- [`CODEX.md`](./CODEX.md) — coding-agent project handoff and next milestones.
- [`ARTEFACT.md`](./ARTEFACT.md) — implementation history, evaluation results, limitations, and deployment status.

## Contributions

The team should record actual ownership, meaningful prototype changes, and reproducible evaluation results. Prototype tags should correspond to working, tested versions.

## Licence

The project licence has not yet been selected. Confirm the team's intended licence and any third-party dependency requirements before adding a licence file.
