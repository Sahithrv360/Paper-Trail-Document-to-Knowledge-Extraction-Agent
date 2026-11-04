# Paper Trail MVP — Current Approach

**Status reviewed:** 4 November 2026

Paper Trail is an MVP that turns invoice files and related business communications into reviewable records, a knowledge graph, and evidence-grounded answers. The current slice demonstrates a local invoice workflow; it is not a production-ready or enterprise-wide platform. This document describes the current repository build; the [software design](docs/SDD.md), [implementation guide](docs/IMPLEMENTATION_GUIDE.md), [milestone report](docs/MILESTONES_REPORT.md), and [build plan](docs/build-plan.md) provide additional detail.

## What the current build does

- Watches a configurable local folder and tracks file versions and extraction jobs.
- Accepts bounded image and PDF input. Extraction options include Donut, local OCR with Tesseract or RapidOCR, and a hosted vision provider that requires explicit opt-in.
- Normalizes invoice fields and line items, validates structure and arithmetic, attaches source evidence when the provider supplies it, and estimates a rule-based quality score. Extraction confidence is a triage score, not a calibrated probability; a high score does not itself approve a document.
- Stores extraction history, evidence, review decisions, and the deterministic invoice graph in SQLite. Records remain reviewable; approval and correction are human actions.
- Can project the SQLite graph into Neo4j. The graph includes documents, invoices, vendors, customers, and line items, with exact shared vendor, customer, and normalized item-description links. SQLite remains the source of truth.
- Provides a local review server and the React Cognix-style frontend. The frontend includes dashboard/analytics, document review, knowledge graph search and visualization, evidence details, and an Ask Knowledge page. The graph uses Sigma.js. The review server is available on loopback at port 8765 by default; the Vite frontend runs on port 5173.
- Answers graph questions in standard retrieval and Agent modes. Retrieval is bounded and read-only. The agent can report its query stages and citations; Ollama is the local generation option, and OpenRouter is an explicitly configured hosted alternative. A deterministic line-item aggregation path counts exact normalized descriptions across invoices without asking the LLM to calculate them.
- Includes synthetic invoice and email examples for development. Synthetic communications are labeled and must not be represented as real payment or approval evidence.

The current development `.env` selects RapidOCR for extraction and local Ollama with `qwen2.5:3b-instruct` for graph answers and Agent responses. A clean checkout uses `.env.example`, which leaves extraction queue-only until a provider is installed and enabled. The full model and installation list is in [`docs/build-plan.md`](docs/build-plan.md) and the [new-device guide](docs/SETUP_NEW_DEVICE.md).

## Data and graph rules

SQLite is authoritative. Neo4j is a rebuildable projection; do not make model-generated graph writes or execute model-generated Cypher. Use the existing ontology and stable source IDs. The core supported paths are:

```text
(Document)-[:SUPPORTS]->(Invoice)
(Vendor)-[:ISSUED]->(Invoice)
(Invoice)-[:BILLED_TO]->(Customer)
(Invoice)-[:CONTAINS]->(LineItem)
```

Invoice identity is tied to the source document, while the invoice number is its display label. Shared parties use deterministic match keys. An item link means the normalized item description matches exactly; it does not establish that the underlying products are identical. Do not add fuzzy entity merges or product taxonomy claims without supporting data and evaluation.

By default, question answering uses reviewed evidence. Synthetic demo records are an explicit exception so the demo conversations remain queryable, and answers must label them as synthetic. Unreviewed records can be included through the UI option and must remain identified as unreviewed. If evidence is missing, conflicting, or only asks about an event, the answer must not report that event as completed.

## Model and privacy boundaries

Question planning, evidence selection, and verification are performed by the local agent workflow. A configured model phrases the final response; it does not select arbitrary graph queries or write facts. The standard Graph RAG answerer supports local Ollama and an explicitly consented hosted provider. Agent mode supports local Ollama or an explicitly configured OpenRouter endpoint. Keep Ollama bound to loopback for local data. Hosted invoice extraction and hosted graph answering have separate opt-ins; never place credentials or private invoice records in source control.

## MVP boundary and next work

The MVP's main remaining checks are a reproducible labeled evaluation of the current extraction paths and a clean end-to-end run with the target machine's invoice data and chosen local services. The agent query examples are manual checks, not a scored benchmark. Master-data connectors, robust cross-system entity resolution, verified product identity resolution, production authentication/deployment, and contract processing beyond the invoice workflow are outside this MVP.

## Useful local commands

From the repository root:

```powershell
python -m backend.app.review.server
```

In another terminal, run the frontend:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173` and use the review token printed by the backend. Neo4j and Ollama are optional for the local SQLite-backed experience; configure their settings in the ignored `.env` file to use those services. See [frontend setup](frontend/README.md) and [environment settings](.env.example).
