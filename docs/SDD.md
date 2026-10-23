# Software Design Document — Paper Trail

**Status:** Working design for the next implementation phases
**Date:** 2026-10-21
**Purpose:** Give contributors and coding assistants a faithful description of the current prototype and a safe path to an evaluated invoice-to-knowledge-graph system.

## 1. Purpose and Scope

Paper Trail monitors a configured folder of procurement documents, extracts structured invoice and contract facts, preserves the source and evidence for each fact, and presents supported entities and relationships as a knowledge graph. The first useful end-to-end target is invoice processing. Contract extraction, RAG-assisted extraction, graph display, and live updates are later capabilities.

This is a prototype and evaluation project. Do not describe the current repository as a production-ready system. Do not train a foundation model from scratch. Start with the existing pretrained Donut model and compare any replacement or additional model against it.

## 2. Current Implementation

- `backend/app/ingestion/` monitors a folder, waits for files to become stable, hashes content, and records document versions, jobs, retries, moves, and deletions in SQLite.
- `backend/app/extraction/schema.py` maps Donut labels into a canonical invoice record, normalizes numeric values, and keeps unavailable values null.
- `backend/app/extraction/donut.py` loads the pinned `katanaml-org/invoices-donut-model-v1` checkpoint and extracts structured labels from an image.
- `backend/app/extraction/evaluate.py` evaluates local image/annotation pairs and produces JSON metrics plus a Markdown side-by-side comparison.
- `backend/tests/` contains unit tests for ingestion, schema mapping, and evaluation helpers.
- The downloaded dataset is `katanaml-org/invoices-donut-data-v1`: 425 training, 50 validation, and 26 test invoices. Local data is in the git-ignored `data/raw/` folder.
- On five evenly spaced test rows (0, 6, 12, 19, 25), the existing Donut run scored 28/75 exact field matches (37.3%) and 0/5 exact documents. Invoice number, date, and customer tax ID were 5/5; subtotal was 4/5; all line-item fields were 0/5 exact. Details are in `reports/test-5.json` and `reports/test-5.md`.

Since slice 2, queued watcher jobs can be run through extraction and graph synchronization (opt-in; see 2b). There is still no LLM/RAG integration in the running pipeline, graph API, OCR fallback for PDFs, or frontend graph view.

## 2a. Implementation Status Update (vertical slice 1)

Implemented and unit-tested (27 tests passing, no model runtime needed):

- Lazy optional imports so the suite runs without `huggingface_hub`/`torch`.
- `extraction/providers.py`: provider interface, `DonutProvider` (baseline), deterministic `FakeProvider`, prompt builder, tolerant JSON parsing, and `extract_validated` (schema + arithmetic checks, provenance incl. retrieval IDs).
- `extraction/rag.py`: training-split-only retriever (rejects validation/test). It returns record IDs and *format shapes* (digits->9), never values.
- `extraction/evaluation.py`: aligned line-item precision/recall/F1 alongside strict exact-list scores.
- `extraction/compare_rag.py`: runnable no-RAG vs RAG comparison on identical validation rows.
- `graph.py`: SQLite graph (Document/Invoice/Vendor/Customer/LineItem; SUPPORTS/ISSUED/BILLED_TO/CONTAINS) with per-document support, idempotent sync, and deletion that preserves shared entities. JSON export for a future UI.

Not done at slice 1: no validation score was produced by that patch. Donut ignores text context, so RAG needs a prompt-capable VLM/LLM provider plus an OCR/first-pass text source for the retrieval query; neither is wired. Slice 2 now connects the watcher queue to image extraction and graph synchronization. No frontend exists in the repository (verified: no package.json or UI directory).

## 2b. Implementation Status Update (slice 2: queue -> extraction -> graph)

`backend/app/pipeline.py` (`JobProcessor`) consumes the registry's queued jobs:

- **process job:** loads the image, runs the configured provider through `extract_validated` (schema + arithmetic checks), then calls `GraphStore.sync` and marks the job complete. The graph is touched only after validation succeeds, so any failure leaves the last valid graph state intact. The validated record, its warnings, and provenance (document ID, version, content hash, source file name, provider, model revision, prompt, retrieval IDs) are stored per document and readable via `GraphStore.get_extraction`.
- **delete job:** removes only that document's support; shared vendors/customers survive while another document supports them.
- **Review policy for this phase:** every stored extraction and `Invoice` node is `needs_review`, even when the arithmetic checks pass. Nothing is auto-approved; warnings are kept for the reviewer. Reviewer approval/correction is not implemented.
- **Stale and restored work:** a process job whose content hash no longer matches the registry, or whose document is deleted, is skipped (`skipped_superseded`). A delete job whose document was restored first is skipped (`skipped_restored`).
- **Failures:** malformed output, provider errors, and files that changed after queueing are retried up to 3 attempts, then `failed`. PDFs fail permanently on the first attempt ("not implemented"); there is no OCR/PDF path.
- **Opt-in runtime:** `main.py` keeps the original queue-only behaviour by default. `EXTRACTION_PROVIDER=donut` (plus `EXTRACTION_DEVICE`, `GRAPH_PATH`) enables local Donut extraction; it needs the optional dependencies and downloads a large checkpoint. Each cycle scans, then drains the queue in the same thread, so a slow extraction delays the next scan.
- **Registry fixes (`ingestion/registry.py`):** `UNIQUE(document_id, content_hash, action)` previously swallowed the job for a file that was deleted and restored unchanged, and a second deletion of the same content. Both would have left the graph wrong. Those completed/failed/cancelled jobs are now re-queued on reactivation or re-deletion; `get_document` was added. Existing watcher tests are unchanged and pass.

Known limitations: a crash while a job is `processing` leaves it stuck (no stale-claim recovery); the extraction store is written in the same transaction as the graph, but job completion is a separate step (re-sync is idempotent, so retry is safe); jobs run sequentially; there is no OCR source, no prompt-capable model, and Donut ignores retrieval context.

## Invoice Item Schema and Validation Update

- Canonical line items now expose `net_amount`, `vat_amount`, and `gross_amount`; `unit_price` follows the Donut dataset's `item_net_price` label. Donut's `item_net_worth` maps to net, `item_gross_worth` to gross, and `item_vat` remains a rate. If the source has no VAT amount but has printed net and gross amounts, VAT is calculated as their difference and marked in `field_origins`.
- The old `amount` property is deprecated. For newly normalized Donut labels it retains the previous gross-first choice and includes `amount_semantics` (`gross`, `net`, or `unknown`). Historical SQLite JSON records are exposed with new amounts null and old `amount` untouched/unknown; no destructive migration or assumption is made.
- Arithmetic checks use decimal currency rounding and compare quantity × net unit price to `net_amount`, VAT rate to `vat_amount`, net plus VAT to `gross_amount`, and item sums to invoice totals. Printed values are never changed. Validation status distinguishes reconciled, mismatch, incomplete, and ambiguous records; all still require human review.
- The supplied `invoice-242.jpg` fixture is covered by mocked-provider regression tests. It does not demonstrate real Donut accuracy. The Donut decoder now allows up to the checkpoint's 1,536 decoder positions (previously 768) and records raw model output, generated-token count, EOS presence, and possible truncation in local provenance. No OCR fallback is installed, and natural-language prompt guidance is not consumed by Donut.
- Party labels are split into name and address only when the model output contains an explicit newline. The source dataset combines these fields, so the adapter does not guess a split from commas or a one-line label. Confidence and field evidence remain null unless a provider returns them; review reasons state that limitation.
- Reprocessing a record marked `approved` or `corrected` is blocked unless a caller explicitly opts in to replacement. No frontend/API is present; `GraphStore.get_extraction()` is currently the inspection path.
- Use `python -m backend.app.reprocess --path watched/invoice-242.jpg` to explicitly rerun a registered image at the same document version and print its stored extraction. Same-version replacement is opt-in through this command; reviewed records remain protected.

**Scope guard for the next contributor or coding assistant:** do not run a RAG comparison and do not claim any accuracy gain yet. `compare_rag.py` exists, but with Donut its "rag" arm is a no-op control, and there is no OCR/first-pass text source or prompt-capable model to feed retrieval. RAG is not wired into the processor. The only measured result remains the five-row test baseline (28/75 exact fields, 0/5 documents). The next steps are an OCR/PDF text source, a prompt-capable provider, and the reviewer surface.

## 3. Requirements

### Functional

1. Ingest PDF, JPG, and PNG files from a configured watched folder; make processing idempotent by content hash and document version.
2. Extract customer name/address/phone/email, invoice number and amount, and multiple line items where present. Also extract optional dates, vendor, tax IDs, subtotal, tax, currency, PO number, and payment terms.
3. Return a validated JSON object. Use `null` for missing or unreadable values; never invent them.
4. Track each extracted fact to its source document ID, version/hash, page (when available), source text or image region (when available), method/model version, and review status.
5. Use RAG only as supporting context. Retrieved examples or rules must never override visible evidence in the current invoice.
6. Create typed graph nodes and relationships, with each claim and edge linked to one or more supporting source documents. Reprocessing or deleting a source must reconcile only that source's support.
7. Provide an inspectable comparison of source file, extracted values, expected values (for labelled evaluation data), and field-level correctness.

### Non-functional and safety

- Keep secrets out of code, reports, and archives. A hosted model must be explicitly configured; do not transmit real invoices by default.
- Use synthetic/public sample documents during development. Preserve dataset attribution and license notes.
- Keep model output untrusted until schema, arithmetic, and evidence checks pass or a person reviews it.
- Make model/provider, prompts, retrieval settings, and model revisions reproducible in reports.
- Keep failures retryable and preserve the last valid extraction/graph state.

## 4. Proposed Processing Architecture

```text
Watched folder
  -> stable-file check, type/size validation, SHA-256, document registry
  -> digital PDF text extraction or OCR/layout recognition for images/scans
  -> optional retrieval of schema guidance and approved training examples
  -> vision-language or OCR-plus-LLM extraction into the canonical invoice schema
  -> deterministic validation, arithmetic checks, evidence links, review status
  -> extraction/provenance persistence
  -> idempotent graph synchronization
  -> API/UI: status, source document, evidence, fields, graph, and evaluation
```

Keep the extraction interface replaceable. The existing Donut adapter is the no-RAG baseline. An LLM/VLM adapter may be local or hosted, but must use the same input fixtures, output schema, and evaluation. RAG context should initially come only from the training split and explicitly approved examples. Do not index validation or test annotations as retrieval content. Start with a small top-k and log retrieved record IDs so retrieval effects can be audited.

The user referenced LangChain's Graph RAG integration: <https://docs.langchain.com/oss/python/integrations/retrievers/graph_rag>. It describes retrieval/traversal over a vector store using shared metadata. It can be evaluated later for document retrieval; it is not a persistent knowledge graph database or a graph visualization. Do not install it unless the chosen retrieval task needs it.

## 5. Initial Knowledge Model

### Node types

- `Document`: stable ID, path, SHA-256, version, processing state.
- `Invoice`: invoice number, dates, subtotal, tax, total, currency.
- `Customer` and `Vendor`: observed name and available contact/tax details.
- `LineItem`: description, quantity, net unit price, net/VAT/gross amounts, tax rate, source row; deprecated `amount` remains with explicit/unknown semantics.
- Future types: `PurchaseOrder`, `Contract`, `Product`, and `PaymentTerm`.
- `Fact` (optional explicit node): field/value plus provenance and review state.

### Relationships

- `(Document)-[:SUPPORTS]->(Invoice or Fact)`
- `(Vendor)-[:ISSUED]->(Invoice)`
- `(Invoice)-[:BILLED_TO]->(Customer)`
- `(Invoice)-[:CONTAINS]->(LineItem)`
- `(Invoice)-[:REFERENCES]->(PurchaseOrder)` only when the invoice provides evidence.

Use stable IDs and normalized keys for entity matching. Do not merge parties from fuzzy name similarity alone. Retain source-specific spellings and provenance. A taxonomy can later classify products, but the first graph needs a small ontology of entity types and relationships, not a large external taxonomy.

## 6. Storage and UI Direction

Reuse SQLite for the current document/job prototype until a demonstrated need requires another service. Add normalized extraction/provenance tables before adding a database server. For a small local graph preview, a deterministic graph export (for example, JSON nodes/edges consumed by a React graph component) is adequate. Choose Neo4j only if persistent graph traversal, operational queries, or the team deployment justifies its setup. Do not replace or rewrite an existing frontend without first locating and inspecting it.

The UI should let a reviewer open a node or edge, see the source filename and version, inspect the extracted value and evidence, review warnings, and correct/approve values. Graph updates should be incremental and must not delete shared entities when one document is removed.

## 7. Evaluation Plan

1. Run existing unit tests and preserve the five-record Donut test report as the baseline.
2. Use the 50 validation records for model, prompt, OCR, and RAG experiments. Compare no-RAG and RAG on identical rows and report exact match, normalized numeric match, coverage, per-field scores, and whole-document scores.
3. Measure line items as aligned item-level precision/recall/F1 or a clearly documented matching score as well as strict exact-list accuracy. Do not count a completely wrong list as the only useful diagnostic.
4. Inspect failure examples and retrieval provenance. Reject retrieval results that introduce values absent from the current document.
5. Freeze model, prompt, and retrieval settings; then evaluate all 26 test rows once for a final benchmark. The model card and shared dataset provenance make this a limited benchmark, not proof of production accuracy.
6. Test graph invariants separately: duplicate events, changed facts, shared support from multiple documents, deletion, and failed processing.

Do not claim accuracy improved until a reproducible validation run shows it. Test-set results must remain final and must not guide tuning.

## 8. Suggested Implementation Slices

1. Fix and run the existing comparison/unit tests; preserve a reproducible no-RAG baseline.
2. Add OCR/PDF text extraction with page and source references, and compare it with Donut on the validation split.
3. Define a provider-neutral VLM/LLM interface and structured-output validation. Keep credentials in environment variables; use a stub in tests.
4. Add RAG over training/approved examples only. Benchmark RAG versus no-RAG on the same validation records.
5. Persist extraction and provenance, then synchronize typed graph nodes/edges idempotently.
6. Add a graph/read API and a small review UI; connect folder jobs to extraction and graph sync.
7. Run the held-out test split after settings are frozen; document limitations and operating instructions.

## 9. Acceptance Criteria for the First End-to-End Prototype

- A stable supported file placed in the watched folder reaches a completed, inspectable extraction job.
- The result validates against the canonical schema; unknown fields are null.
- Each displayed field identifies its source document, and available page/snippet evidence is viewable.
- Reprocessing the same version creates no duplicate nodes or edges; updates and deletion reconcile only that source's contribution.
- A graph view shows typed invoice, party, line-item, and document relationships and allows source inspection.
- Tests cover successful extraction, malformed model output, retry, duplicate processing, changed files, deletion, and shared entities.
- Validation results compare the baseline and any RAG/model alternative on the same examples. Final test scores are reported separately.

## 10. Local Commands

```powershell
python -m unittest discover -s backend/tests -v
python -m backend.app.extraction.evaluate --split test --limit 5 --device cpu --output reports/test-5.json
python -m backend.app.extraction.evaluate --split validation --limit 50 --device cpu --output reports/validation-baseline.json
python -m backend.app.main                      # queue only (default)
$env:EXTRACTION_PROVIDER="donut"; python -m backend.app.main   # opt-in extraction + graph
python -m backend.app.reprocess --path watched/invoice-242.jpg   # explicit same-version rerun
```

Extraction dependencies are in `backend/requirements-extraction.txt`. The model checkpoint is large; the downloaded dataset and local reports are git-ignored. See `README.md`, `data/README.md`, `docs/functional.md`, and `docs/IMPLEMENTATION_GUIDE.md` for complementary project context.
