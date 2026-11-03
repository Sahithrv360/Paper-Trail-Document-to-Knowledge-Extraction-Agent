# Presenter Dataset

This folder contains ten fictional invoice records and a relationship manifest for demonstrating Paper Trail's invoice graph.

## Contents

- `synthetic_invoices/`: one JSON file per invoice, with fields matching the extraction record shape.
- `relationships.json`: expected shared-vendor, shared-customer, and exact-item-description groups.

The records use fictional organizations, example invoice numbers, USD amounts, and internally reconciled line totals. Relationships are intentionally varied: invoices can share more than one fact, while others connect through only one basis.

These are structured demo records, not OCR-ready images. Their JSON source files stay outside `watched/` and the live application databases; the loader below creates a dedicated SQLite demo graph.

## Build the presenter graph

From the repository root, run:

```powershell
python -m backend.app.demo
```

This resets only `presentation_data/runtime/paper_trail_graph.sqlite3`, loads all ten records, and marks nine as approved. `DEMO-2026-010` stays unreviewed so the reviewed-only chat behavior can be demonstrated. The data includes a capitalization variant of Northstar Office Supply and shared customers and item descriptions.

To open the UI on this demo graph without requiring Neo4j, use a second PowerShell window:

```powershell
$env:GRAPH_PATH = "$PWD\presentation_data\runtime\paper_trail_graph.sqlite3"
python -m backend.app.review.server
```

The graph view and deterministic chat then read directly from the local SQLite graph. To use an already-installed Ollama model for other graph questions, set `GRAPH_RAG_BACKEND=ollama` and `GRAPH_RAG_MODEL` before starting the server; Ollama must be running locally.

To also replace the PaperTrail-owned projection in your configured Neo4j database, explicitly add `--sync-neo4j`:

```powershell
python -m backend.app.demo --sync-neo4j
```

That option uses `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and optional `NEO4J_DATABASE`. It does not modify unrelated Neo4j nodes or relationships. These records are structured JSON, not scanned invoice images, and do not measure OCR accuracy.
