# Paper Trail — Document-to-Knowledge Extraction Agent

Paper Trail is a local-first invoice-processing MVP. It ingests invoice files, extracts structured fields with evidence where available, keeps results reviewable, builds a graph, and answers business questions from linked records.

> **MVP status (4 November 2026):** The repository includes watched-folder ingestion, bounded image/PDF handling, Donut and OCR extraction options, an opt-in hosted vision extractor, a versioned SQLite ledger and graph, local review tools, an optional Neo4j projection, a React dashboard/knowledge-graph/Ask Knowledge UI, and standard and agentic graph QA. The MVP workflow is implemented, while representative labeled evaluation and a clean end-to-end validation on the target machine remain outstanding. This is not a production-ready system. See the [current approach](APPROACH.md), [build plan](docs/build-plan.md), and [milestone report](docs/MILESTONES_REPORT.md).

The current development `.env` is configured for RapidOCR extraction and local Ollama answer generation with `qwen2.5:3b-instruct`. The checked-in `.env.example` is safer for a fresh setup and defaults extraction to queue-only; set the provider and install its optional dependencies to enable extraction.

## What it does

- Watches a configurable folder and tracks document versions and processing jobs.
- Extracts invoice fields and line items using a selected local or explicitly enabled hosted provider.
- Validates output and stores source evidence, provenance, confidence triage, and review history.
- Builds a deterministic SQLite graph and can sync a rebuildable projection to Neo4j.
- Links invoices through shared vendors, customers, and exact normalized item descriptions. An item text match does not prove product identity.
- Shows documents, review details, live analytics, graph search, evidence, and graph-grounded answers in the local UI.
- Provides deterministic common-item counts across invoices, plus standard graph retrieval and an Agent mode that reports query progress and citations.

SQLite is the source of truth. Neo4j is optional. The agent is read-only; its model phrases answers and does not write graph facts. Synthetic/demo emails are labeled and are not real payment or approval evidence. Confidence scores are rules-based triage values, not calibrated probabilities or automatic approvals.

## Quick start (Windows)

For the complete new-machine guide, including all model downloads, optional OCR/Neo4j setup, privacy settings, and data transfer, see [`docs/SETUP_NEW_DEVICE.md`](docs/SETUP_NEW_DEVICE.md).

Prerequisites: Git, Python 3.11+ (Python 3.12 recommended), Node.js LTS, and Ollama if you want local LLM answers.

### 1. Clone and install the Python agent package

```powershell
git clone https://github.com/Sahithrv360/Paper-Trail-Document-to-Knowledge-Extraction-Agent.git
cd Paper-Trail-Document-to-Knowledge-Extraction-Agent
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
Copy-Item .env.example .env
```

Set `WATCH_FOLDER` in `.env` to the local folder where invoices should be watched. Keep the file private.

### 2. Pull the default local chat and embedding models

Install and start Ollama, then run:

```powershell
ollama pull qwen2.5:3b-instruct
ollama pull nomic-embed-text
```

`qwen2.5:3b-instruct` is the model named in `.env.example` for local answer generation. `nomic-embed-text` is used for dense retrieval embeddings. These are separate models and both must be available to Ollama for those paths. You can select another installed chat model using `GRAPH_RAG_MODEL`.

The `.env.example` also contains `qwen/qwen3.8-flash` as an optional OpenRouter model ID. OpenRouter needs an API key, and retrieved invoice evidence leaves the device when that provider is used. The local default is Ollama.

### 3. Start backend and UI

Terminal 1, from the repository root:

```powershell
python -m backend.app.review.server
```

Terminal 2, from the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

The backend prints a token link on port `8765`. Copy its `#token=...` fragment into `http://localhost:5173/#token=...` to open the new React UI. Vite forwards API calls to the backend. Keep the token private.

## Model and engine inventory

| Task | Default/available choice | Install or enable |
|---|---|---|
| Invoice extraction | Pinned Donut checkpoint `katanaml-org/invoices-donut-model-v1` | `python -m pip install -r backend/requirements-extraction.txt`; set `EXTRACTION_PROVIDER=donut`. First inference downloads model weights. |
| Invoice OCR and field mapping | Tesseract executable | Install the Tesseract program separately; install `backend/requirements-ocr.txt` and `backend/requirements-pdf.txt`; set `EXTRACTION_PROVIDER=ocr`, `PARSER_ENGINE=tesseract`. |
| Optional OCR | RapidOCR ONNX Runtime | `python -m pip install -r backend/requirements-rapidocr.txt`; set `PARSER_ENGINE=rapidocr`. It uses more memory than Tesseract in the recorded fixture run. |
| OCR/layout inspection | PaddleOCR PP-StructureV3 (`en_PP-OCRv4_mobile_rec`) | `python -m pip install -r backend/requirements-paddleocr.txt`; standalone preview only, not the watched-folder invoice mapper. |
| Local graph answer / Agent wording | Ollama model `qwen2.5:3b-instruct` in `.env.example` | Install Ollama, then `ollama pull qwen2.5:3b-instruct`; configure `GRAPH_RAG_BACKEND=ollama`. |
| Dense retrieval | Ollama `nomic-embed-text`; optional local fallback `BAAI/bge-small-en-v1.5` | `ollama pull nomic-embed-text`; the Sentence Transformers fallback is optional and documented in `pyproject.toml`. |
| Hosted Agent wording | OpenRouter model ID `qwen/qwen3.8-flash` in `.env.example` | Set `AGENT_LLM_PROVIDER=openrouter`, `OPENROUTER_MODEL`, and `OPENROUTER_API_KEY`. Verify model availability before use. |
| Graph database | SQLite; optional Neo4j | Neo4j needs Docker/Neo4j and `python -m pip install -r backend/requirements-neo4j.txt`. |

The actual provider and model are determined by `.env` and the machine's installed software. No model weights or API credentials are committed. Hosted invoice extraction and hosted graph answering require separate explicit opt-ins.

## Optional setup

### Local invoice extraction

The app defaults to `EXTRACTION_PROVIDER=none`, which queues files without running a model. To use Donut or OCR, install the corresponding requirements in the active virtual environment and set the provider in `.env`. PDF text extraction/rendering uses `backend/requirements-pdf.txt`. Extraction remains subject to human review; a completion score is not approval.

### Neo4j

Neo4j is optional. Start a local Neo4j instance with Bolt port `7687` available, set `NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD` in `.env`, then:

```powershell
python -m pip install -r backend/requirements-neo4j.txt
python -m backend.app.neo4j_sync
```

SQLite remains authoritative, so the UI can use the SQLite graph if Neo4j is unavailable. Re-sync after importing or reviewing records.

## Development checks

```powershell
python -m unittest discover -s backend/tests -v
```

This suite is not a substitute for labeled extraction or business-QA evaluation. Follow [`docs/EVALUATION.md`](docs/EVALUATION.md) and record actual run configuration and results before making accuracy claims.

## Repository guide

- [`APPROACH.md`](APPROACH.md) — current implementation and design rules.
- [`docs/build-plan.md`](docs/build-plan.md) — current status, model inventory, and next validation sequence.
- [`docs/SETUP_NEW_DEVICE.md`](docs/SETUP_NEW_DEVICE.md) — install and run locally on a new device.
- [`docs/SDD.md`](docs/SDD.md) — detailed system design and implementation notes.
- [`docs/DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md) — extraction, evidence, ledger, and graph contracts.
- [`docs/DATA_HANDLING.md`](docs/DATA_HANDLING.md) — local and hosted processing boundaries.
- [`docs/EVALUATION.md`](docs/EVALUATION.md) — benchmark workflow and metric definitions.
- [`docs/MILESTONES_REPORT.md`](docs/MILESTONES_REPORT.md) — delivered milestones and known gaps.
- [`docs/system_architecture.drawio`](docs/system_architecture.drawio) and [`docs/graph_rag_message_flow.drawio`](docs/graph_rag_message_flow.drawio) — editable architecture diagrams.
- [`docs/agent_query_examples.md`](docs/agent_query_examples.md) — sample queries and manual checking guide.
- [`docs/prototype.html`](docs/prototype.html) — static UX sketch; the live UI is in `frontend/`.

## Privacy

Keep `.env`, API keys, review tokens, real invoices, local SQLite databases, vector indexes, and conversation logs out of Git. Ollama and local OCR can keep processing on the device. Hosted model routes transmit the question and retrieved evidence or document content to the configured provider; use them only when that transfer is permitted.
