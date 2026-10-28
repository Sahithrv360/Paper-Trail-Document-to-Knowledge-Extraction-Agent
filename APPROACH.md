# Paper Trail — Approach & Build

This document describes the agreed MVP direction. Paper Trail turns invoices into reviewable, evidence-linked facts and a graph that users can inspect and query. The implementation record is in [`docs/SDD.md`](docs/SDD.md), with data rules in [`docs/DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md) and current milestones in [`docs/build-plan.md`](docs/build-plan.md).

## MVP Scope

The MVP focuses on invoices. It accepts files through the existing watched-folder workflow, uses the current extraction providers, validates the result, records evidence and review history, and builds typed invoice relationships. OCR/model accuracy improvement is not the active phase; use the extracted details already available to complete and verify graph mapping.

The current backend stores its source ledger and deterministic graph in SQLite. The local review server exposes document details, review actions, provenance, and review status. An optional Neo4j projection can be synced from SQLite and read by the graph view and chat; configure Neo4j locally before using those features. SQLite remains the source of truth.

## Graph Mapping

Use the existing ontology and stable IDs from `backend/app/graph_ontology.py`:

```text
(Document)-[:SUPPORTS]->(Invoice)
(Vendor)-[:ISSUED]->(Invoice)
(Invoice)-[:BILLED_TO]->(Customer)
(Invoice)-[:CONTAINS]->(LineItem)
```

Invoice identity uses the document ID, because invoice numbers can repeat. Vendor and customer nodes are shared only when the existing exact match key agrees; do not fuzzy-merge parties. Line items belong to one invoice and use document ID plus row position. Cross-invoice paths emerge through shared, supported vendors or customers. Do not invent item similarity or taxonomy links.

The Neo4j sync must be repeatable, preserve node and edge provenance/review status, and remove stale Paper Trail projection facts safely when a source version changes. Only allow the known ontology labels and relationship types. Never execute model-generated Cypher or accept model output as a graph write.

## Graph RAG and LLM

The Graph view and chat share the local review app. A bounded, read-only retriever searches Neo4j and expands relevant nodes to their connected parties, line items, and source documents. The LLM receives only that retrieved subgraph and returns a concise answer with invoice/document IDs and review status as citations. If the retrieved facts do not support an answer, it must say so.

Use parameterized, allowlisted Cypher templates for retrieval; do not ask the LLM to write queries. Default answers should use approved or corrected facts. If unreviewed facts are included, label them clearly. The LLM may explain retrieved relationships, but must not create, merge, correct, or delete graph facts. Begin with lexical/structured graph retrieval; add embeddings only if evaluation shows that exact and lexical matching cannot find relevant item descriptions.

Hosted generation is off by default. Add a separate explicit opt-in for sending retrieved invoice details to a hosted model, reuse configured model credentials where suitable, and never log prompts or answers containing invoice data. A local model can be considered later if hardware and latency measurements support it.

## Delivery Order and Exit Checks

1. **Neo4j projection:** implemented as a repeatable CLI and review-app action, with stable IDs, typed edges, support status, and stale-fact cleanup. Still needs a run against the configured local Neo4j instance.
2. **Graph and chat UI:** implemented in the existing local review app. The graph is read from Neo4j; answers use bounded retrieval, link to source invoice/document nodes, and abstain when facts are missing. Hosted LLM generation is optional and separately gated.
3. **End-to-end check:** pending. Sync and inspect existing extractions, review/approve records, and confirm chat answers reflect the reviewed graph and source documents. Record the tested commands and remaining limits for teammates.

Tests should cover mapping, edge direction, repeat sync, stale-fact cleanup, review filtering, retrieval boundaries, citations, and abstention. Do not claim extraction accuracy improvements in this phase. Keep `.env`, credentials, customer invoices, database files, and local evaluation artifacts out of Git.

## Existing Capabilities and Deferred Work

The repository already contains folder ingestion, Donut/Tesseract/RapidOCR and an opt-in vision API path, canonical invoice schema, arithmetic/evidence validation, SQLite ledger and graph, local reviewer UI, and a benchmark harness. Dataset-based OCR/model comparison has not been established; this phase does not change that. Defer contracts, production deployment, enterprise connectors, product taxonomy, automatic fuzzy entity resolution, and graph writes from LLM output until the invoice MVP is demonstrated.
