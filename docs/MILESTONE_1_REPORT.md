# Paper Trail: Milestone 0 Audit and Milestone 1 Report

Date: 24 October 2026. Scope: Milestone 0 audit plus Milestone 1 (data contracts, ledger, deterministic graph), as gated by `CLAUDE_BUILD_PROMPT_V2.md`. Work stopped there. No API, OCR, UI, RAG or evaluation work was started.

## Milestone 0: audit findings

**What was inspected.** Everything in the handoff ZIP: all backend source, tests, docs, the `invoice-242.jpg` fixture and its attribution, and the three historical reports. The user's Windows laptop was not inspected; nothing here describes its hardware or runtime.

**Tree.** This matches the handoff description: `backend/app/{ingestion,extraction}`, `pipeline.py`, `graph.py`, `main.py`, `reprocess.py`, and stdlib-`unittest` tests. There is no frontend, API, migrations or `.env.example`. `assests/CODEX_BUILD_PROMPT.md` is historical. `README.md` "Repository Structure" still describes a planned `src/paper_trail/` layout that does not exist; it was left unchanged, as it is pre-existing and outside Milestone 1.

**Baseline tests.** `python -m unittest discover -s backend/tests -v` gave 47 run, 47 OK (Linux container, Python 3.13.17). This matches the user-reported 47. None of these exercise live Donut or PaddleOCR inference.

**Data.** `data/raw/` (the 501-image dataset) is not in the handoff, so no evaluation could be run. The fixture's licence attribution (CC BY 4.0, via `data/README.md`) is present.

**Historical results** (re-read, not re-run). Donut on five test rows: 28/75 exact fields (37.3%), 0/5 exact documents. Python 3.12.10, torch 2.13.0, transformers 4.57.6, CPU.

**Defects found in existing code that the new prompt's rules prohibit:**

1. The Donut mapper filled missing line VAT with `gross − net`, which breaks rule 4 ("never fill").
2. `parse_amount` stripped every non-digit, so OCR noise such as `69A,9O` became `69.9` (a fabricated value).
3. Shared Vendor/Customer node properties were last-writer-wins (`INSERT OR REPLACE`), so the graph depended on processing order.
4. `mark_reviewed` mutated the stored machine record. There was no review history and no review state on support rows.
5. There was no schema versioning or migration path.
6. A record with zero line items got a misleading "line-item sum 0 differs from subtotal" mismatch.

All six are fixed in Milestone 1. One inherited ambiguity is left as is: `parse_amount("1,234")` reads 1234.

**Architecture conflicts requiring user input.** None. Milestone 1 went ahead.

## Milestone 1: what changed

New modules:

| File | Purpose |
|---|---|
| `backend/app/extraction/matching.py` | Exact entity match keys (NFKC, casefold, whitespace, tax-ID separators) and tolerant evidence comparison forms |
| `backend/app/extraction/evidence.py` | `ParseContext`; per-field `raw_text` vs `value`; quote/page/bbox evidence; exact → normalized → OCR-folded → fuzzy match diagnostics; page/geometry validation; printed-precision detection |
| `backend/app/extraction/validation.py` | Structured findings (code, field, 1-based row, fixed status effect); arithmetic at printed precision; independent vs self-reported row counts; classification fallback; multi-page artefact flags; legacy `amount` flag |
| `backend/app/graph_ontology.py` | Allowed node/edge types and directions, stable IDs, deterministic claim builder |
| `backend/app/ledger.py` | Schema and migrations, extraction versions, reviews, claims, materialization, before/after diff, `graph_changes` |
| `docs/DATA_CONTRACTS.md` | Full reference for the record, evidence, findings, identity, ontology and ledger |

Modified files:

| File | Change |
|---|---|
| `extraction/schema.py` | New optional record fields (`doc_type`, `row_count_seen`, `extensions`, `findings`, `field_evidence`, `validation_details`, item `page`); no VAT filling (`derived.vat_amount` is validation-only); hardened number parsing; `arithmetic_warnings` delegates to `validation` and keeps its messages |
| `extraction/providers.py` | `extract_validated(..., parse=None)` adds evidence, structured findings, findings-based status, `PROMPT_VERSION`, `extraction_method` and a parser summary in provenance; schema guidance asks for printed strings, `doc_type`, `row_count_seen` and path-keyed evidence |
| `extraction/evaluation.py` | Item fields whose labels are all null (for example line VAT) are no longer scored |
| `graph.py` | Rebuilt on the ledger. Same public API plus `apply_review`, `export(reviewed_only=)`, `extraction_history`, `synced_documents`, `changes`, `rebuild_graph`, `entity_suggestions` and `duplicate_content_groups` |
| `pipeline.py` | Passes `version` and `run_id=job:<id>` to the graph |
| `ingestion/registry.py` | WAL journal mode |
| `tests/test_invoice_242.py` | One assertion updated: line VAT now stays `null` (deliberate rule change) |
| `tests/test_contracts.py`, `tests/test_ledger_graph.py` | 54 new tests |
| `docs/SDD.md` (§2c), `docs/IMPLEMENTATION_GUIDE.md`, `README.md`, `AGENTS.md`, `data/README.md`, `.gitignore` | Updated to match the implemented behaviour |

## Schema and migration effects

- **Graph database:** `PRAGMA user_version = 2`. New tables: `extraction_versions`, `reviews`, `claims` and `graph_changes`. `edges` gains a `props` column.
- **Upgrading a v1 database:** `extractions`, `support` and `synced` are renamed to `legacy_v1_*` (kept, not dropped). Each stored extraction is imported as an active version, and reviewed ones get a `legacy-import` review row. The graph is rebuilt from claims and a `migrate` ledger row is written. Re-running the migration is a no-op.
- This was verified on a database written by the original, unmodified code: 14 nodes/14 edges before and after, the approval was preserved, and `rebuild_graph()` showed no drift.
- `Invoice` and party node IDs change format (`Invoice:<document_id>`, `Vendor:<hash of match key>`). `export()` and `get_extraction()` keep their keys and only add fields.
- **Registry database:** journal mode switches to WAL. No schema change.
- **Deliberate behaviour changes:**
  - Line VAT is never filled.
  - Garbage inside a number now gives `null` plus a finding.
  - Approving after a correction keeps the correction.
  - Zero-row records are `incomplete_extraction`, not `arithmetic_mismatch`.

## Commands run and raw results

| Command | Environment | Result |
|---|---|---|
| `python -m unittest discover -s backend/tests -v` (baseline, before edits) | Python 3.13.17, SQLite 3.53.1, Linux | 47 run, 47 OK |
| `python -m unittest discover -s backend/tests -v` (final) | Python 3.13.17, SQLite 3.53.1 | 101 run, 101 OK |
| same, final | Python 3.12.3, SQLite 3.45.1 | 101 run, 101 OK |
| same, final | Python 3.11.17, SQLite 3.45.1 | 101 run, 101 OK |
| `python -W error::ResourceWarning -m unittest discover -s backend/tests` | Python 3.13 | 101 OK, no leaked connections |
| `test_ledger_graph.py` ×5 (concurrency stability) | Python 3.13 | 5/5 OK |
| `python -m pyflakes backend/app backend/tests` | — | 2 warnings, both pre-existing in untouched code (`donut.py` unused `torch`; `test_pipeline.py` unused import) |
| v1 → v2 migration of a database built by the original code | Python 3.13 | Same node/edge counts, approval kept, zero rebuild drift |

Per-file counts in the final run: `test_contracts` 28, `test_ledger_graph` 26, `test_job_pipeline` 14, `test_pipeline` 14, `test_extraction` 8, `test_invoice_242` 6, `test_watcher` 5.

All 18 required regression cases are covered, plus evidence tolerance, page/geometry validation, classification fallback, multi-page artefacts, fault-injection rollback, WAL, an eight-thread concurrent-writer test and v1 migration. The randomized union-invariant test runs 6 seeds × 60 mixed operations and checks the full graph and the reviewed-only graph against an independent model after every step.

## Not run

- Any model inference (Donut, PaddleOCR or a VLM), any API call, and any dataset evaluation. The dataset is absent and the gate forbids it.
- Nothing on the user's Windows laptop. CPU, memory and latency are unmeasured; the 2-CPU/7.8 GB Linux container used here is not representative.
- **Measured accuracy:** none. Extraction quality is unchanged from the historical baseline.

## Unresolved issues

1. **No parser is wired.** Evidence checks and the independent row count work only when a `ParseContext` is supplied. Live Donut records report `evidence_missing`, and Donut returns no quotes.
2. **Stuck jobs.** A crash during `processing` still leaves a job stuck, because there is no stale-claim recovery. Jobs run in the scan thread.
3. **Changed reviewed files.** A changed file whose current extraction is reviewed fails its job permanently, which is existing behaviour. Superseding it needs an explicit `allow_reviewed_replace`; a reviewer-facing flow for this belongs in Milestone 3.
4. **"1,234" ambiguity.** `parse_amount` still reads a single comma followed by exactly three digits as a thousands separator.
5. **Jurisdiction fields.** These (GSTIN, HSN/SAC, IFSC, tax components) are only carried as raw `extensions`. The target jurisdiction is still unknown.
6. **PaddleOCR.** The PP-StructureV3 preview's status on Windows is unchanged and unresolved. It was not touched.

## Next milestone (awaiting your review)

**Milestone 2: low-resource adapters.**

- A lazy-loaded RapidOCR-ONNX or Tesseract adapter that returns a `ParseContext` (text, boxes, table row count), running on one page with one CPU worker, with bounded resize and timeout.
- One opt-in vision API provider behind the same interface, chosen after checking current vendor docs for structured-output support, price and retention. It stays disabled unless an explicit allow-remote setting and a key are present.
- A single-invoice CLI with a cost and privacy notice, with both adapters mocked in tests.
