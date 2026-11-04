# Paper Trail MVP — Build Plan

**Status reviewed:** 4 November 2026. This is an MVP build plan. Status describes code present in this repository; it does not imply a fresh end-to-end run on every machine or production readiness.

**Current local configuration:** the existing `.env` selects `EXTRACTION_PROVIDER=ocr` with `PARSER_ENGINE=rapidocr`, `GRAPH_RAG_BACKEND=ollama`, `GRAPH_RAG_MODEL=qwen2.5:3b-instruct`, and `AGENT_LLM_PROVIDER=ollama`. This reflects the current device configuration; a fresh clone starts from `.env.example` and must be configured separately.

## MVP delivery status

| Workstream | Current implementation | Remaining verification or work |
|---|---|---|
| Local intake | Watched-folder ingestion tracks file versions and jobs. JPG, PNG, and bounded PDF input are supported by the document loader. | Run against the target machine's paths and confirm interrupted-job recovery and file limits. |
| Extraction | Provider options include pinned Donut, Tesseract OCR mapping, optional RapidOCR, and explicitly opted-in Anthropic vision extraction. PDF text extraction/rendering is optional. | Run a labelled validation set for the active providers. Do not infer accuracy from OCR confidence or synthetic fixtures. |
| Validation and ledger | Canonical invoice schema, arithmetic checks, evidence/provenance, version history, review decisions, and deterministic SQLite graph are implemented. A rule-based confidence score and completion threshold are exposed for triage. | Confirm the score/status behavior against labeled cases; the score is not a calibrated probability and does not auto-approve records. |
| Review experience | Local token-protected review server and React frontend cover documents, review, evidence, dashboard analytics, graph, and question-answering pages. | Complete an end-to-end pass on the destination computer, including browser token flow and watched-folder updates. |
| Knowledge graph | SQLite is the source of truth. Optional Neo4j sync creates a rebuildable projection. Invoice links are derived from shared vendors, customers, and exact normalized line-item descriptions. | Configure Neo4j credentials and verify sync/read on the actual local instance. Same item text means a description match, not verified product identity. |
| Graph QA and agent | Standard graph retrieval and Agent mode are implemented. Agent planning, retrieval, evidence ranking, verification, progress stages, and conversation logging are connected to the UI. The agent's model only phrases the final answer. | Run the query examples against the current graph and score a labeled business-QA set. Review logs and citations; manual examples are not an evaluation benchmark. |
| Deterministic item counts | Common-item questions use invoice line items and count exact normalized descriptions and invoice-pair links in code. The LLM is not asked to calculate the counts. | Check requested customer/review scope and output table against the current invoice import. |
| Tests and evaluation | Unit tests, extraction benchmark tools, Graph RAG evaluation tools, and synthetic fixtures are in the repository. | Run the full suite and labeled-data evaluation on the machine used for the final demonstration; report exact commands and results. |

## Model inventory

| Purpose | Current model or engine | Where it is configured | Notes |
|---|---|---|---|
| Invoice extraction (local vision) | `katanaml-org/invoices-donut-model-v1`, pinned to revision `1745f5a29c37bc5d2e16ab5e2bc6aba57fdd6f7c` | `backend/app/extraction/donut.py`; enable with `EXTRACTION_PROVIDER=donut` | Downloads through Hugging Face on first use. PyTorch device can be `cpu` or a compatible CUDA device. It does not provide calibrated confidence or field evidence by itself. |
| Invoice extraction (OCR) | Tesseract executable with the project's label/geometry mapper | `TESSERACT_CMD`, `PARSER_ENGINE=tesseract`, `EXTRACTION_PROVIDER=ocr` | Tesseract is a separately installed system program; no invoice-language model download is needed. |
| Optional OCR alternative | RapidOCR ONNX Runtime package and its English OCR weights | `PARSER_ENGINE=rapidocr`, `EXTRACTION_PROVIDER=ocr` | Local CPU option; can use substantial RAM. It is not required if Tesseract is selected. |
| Optional OCR preview | PaddleOCR PP-StructureV3, recognition model `en_PP-OCRv4_mobile_rec` | `backend/app/extraction/paddle_ocr.py` | Standalone inspection tool, not the mapped invoice extraction provider. |
| Hosted invoice extraction | No model ID is pinned in the repository; configure an Anthropic Messages model with `VISION_API_MODEL` | `VISION_API_BACKEND`, `VISION_API_MODEL`, API key, and explicit `PAPERTRAIL_ALLOW_REMOTE` opt-in | Sends document content outside the device; it is disabled unless all required settings and consent are present. |
| Agent answer generation (local default) | `.env.example` sets `qwen2.5:3b-instruct` through Ollama | `GRAPH_RAG_BACKEND=ollama`, `GRAPH_RAG_MODEL`; Agent provider is `AGENT_LLM_PROVIDER=ollama` | Agent mode uses `GRAPH_RAG_MODEL` first, then `OLLAMA_MODEL`, then `qwen3:4b` as a code fallback. The checked-in example's `GRAPH_RAG_MODEL` therefore selects Qwen 2.5 3B for both. |
| Standard Graph RAG hosted answer option | Anthropic Messages API; no provider model ID is pinned | `GRAPH_RAG_BACKEND=anthropic`, `GRAPH_RAG_MODEL`, API key, and `PAPERTRAIL_ALLOW_GRAPH_RAG_REMOTE` | Separate opt-in from hosted invoice extraction; retrieved graph evidence leaves the device. |
| Agent answer generation (hosted option) | `.env.example` contains `qwen/qwen3.8-flash` as the OpenRouter model ID | `AGENT_LLM_PROVIDER=openrouter`, `OPENROUTER_MODEL`, `OPENROUTER_API_KEY` | Optional hosted route; requires a key and sending retrieved graph evidence to that service. Verify the model ID is available in the account before use. |
| Dense retrieval embeddings | `nomic-embed-text` through Ollama; optional fallback `BAAI/bge-small-en-v1.5` through Sentence Transformers | `kg_agent/config.py`; the BGE fallback requires the `embeddings` extra | These are embedding models, not answer-generation models. Ollama should be running locally for the default embedding path. |
| Graph storage | No ML model | SQLite, with optional Neo4j projection | Neo4j is optional for SQLite-backed graph retrieval and visualization fallback. |

Model weights are not committed to Git. Ollama pulls and Hugging Face caches must be downloaded on each new device. For supported local setup, see [New-device setup](SETUP_NEW_DEVICE.md).

## Remaining MVP verification sequence

1. Set up a clean environment using [`SETUP_NEW_DEVICE.md`](SETUP_NEW_DEVICE.md), starting with local SQLite and Ollama. Add Neo4j or extraction providers only when needed.
2. Run the backend and frontend, import or copy the intended development data, and check dashboard counts, document status, graph search, and a deterministic common-item query.
3. If using Neo4j, sync from SQLite and check shared-vendor, shared-customer, and shared-item invoice links. Keep SQLite as the source of truth.
4. Run the full unit suite and the agent query examples. Record model/provider, data scope, reviewed/unreviewed setting, latency, citations, and failures.
5. Run a reproducible labeled validation comparison for extraction and a separately labeled question-answering evaluation. Publish no accuracy or business-effectiveness claim without those results.

## Build principles

1. Do not auto-approve model output. A completion score is triage; review status remains a human decision.
2. Keep evidence and source version attached to claims. Distinguish what a message asks or proposes from a recorded event.
3. Keep retrieval bounded and read-only. Only deterministic application code may write graph facts.
4. Treat exact item-description links as text matches, not product identity resolution.
5. Keep synthetic/demo material labeled; do not treat it as real customer evidence.
6. Keep `.env`, tokens, invoice files, databases, indexes, and local evaluation artifacts out of Git unless a specific public synthetic artifact is intentionally selected.
