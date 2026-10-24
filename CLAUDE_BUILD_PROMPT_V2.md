# Paper Trail: Staged Build Prompt for Claude Code

You are a senior Python document-AI and data-engineering engineer. Continue the supplied Paper Trail repository. Read the repository before editing. Keep existing working behavior, fix root causes, and build a safe invoice-to-knowledge-graph system in reviewable milestones. Do not replace the project wholesale or claim extraction improved until a reproducible labelled evaluation proves it.

## User goal

The user wants to drop invoice files into a watched folder, extract fields accurately without making the laptop hang, inspect which source image/page and evidence supports every extracted value, and see a persistent knowledge graph with correct typed relationships. Prefer an opt-in vision API for strong image understanding on constrained hardware, with a genuinely light local OCR option. Build the graph deterministically from validated extraction output. The user plans to test accuracy later; do not tune against or claim results from unrun evaluations.

## Read and inspect before coding

Read `project/AGENTS.md`, `project/README.md`, `project/docs/SDD.md`, `project/docs/functional.md`, `project/docs/IMPLEMENTATION_GUIDE.md`, `project/data/README.md`, `TECH_RESEARCH.md`, all backend source, tests, and the regression fixture. Inspect the baseline reports. Report the actual current tree, test/evaluation evidence and environment; distinguish current code from plans.

Current implementation facts:

- Python backend in `backend/app/`; folder watcher and SQLite job registry under `ingestion/`; image extraction provider/schema/evaluator under `extraction/`; processing in `pipeline.py`; deterministic SQLite graph in `graph.py`.
- Donut is an existing local provider. Current pipeline supports JPG/PNG only. Every stored extraction remains `needs_review`. Graph synchronization is idempotent and tracks per-document support. The graph currently has Document, Invoice, Vendor, Customer, LineItem nodes and SUPPORTS, ISSUED, BILLED_TO, CONTAINS edges.
- The provider interface, schema/arithmetic validation, historical `amount` compatibility, watcher pipeline, graph, and mocked regression tests should be reused and inspected. Do not silently delete these.
- There is no frontend or API in the repository. Do not assume a React app exists. The old `assests/CODEX_BUILD_PROMPT.md` is conceptual, includes technologies not in the current tree, and is not permission to add them.
- The last reported full unit suite had 47 passing tests, but it predates/does not cover live PaddleOCR inference. State this precisely.
- The last measured Donut five-row test result is 28/75 exact labelled fields (37.3%) and 0/5 fully exact documents. Reports are historical diagnostics, not the new model's score.
- Current PaddleOCR preview uses PP-StructureV3 and has loaded numerous layout/OCR/table models on the user's Windows laptop. The user saw a oneDNN CPU runtime error before a local workaround disabled MKL-DNN; the following invocation returned to PowerShell without a success message or captured exit code. Its runtime status remains unresolved. Do not enable this heavy pipeline in the watcher or default path.
- Dataset `katanaml-org/invoices-donut-data-v1` has 425 train, 50 validation, 26 test rows. Its annotations have European-style VAT labels and combined seller/client text; the separate supplied `invoice-242.jpg` fixture has a printed `$`. The user's target country/jurisdiction for future real invoices is unknown. Do not assume India, Europe, or a currency code.

## Non-negotiable rules

1. Unknown, unreadable, or unsupported values are `null`; never fabricate a row, field, address split, currency, tax ID, quantity, or evidence.
2. Preserve observed source spelling and formatting as `raw_text`. Put parsed/canonical form in a separate `value`/`normalized_value`. The value shown to reviewers must remain the source reading; a normalization key is only for matching.
3. Keep `Vendor` and `Customer` separate. Never move tax/address/bank data across parties. Do not put IBAN/IFSC in address.
4. Distinguish printed `net_amount`, `tax_rate`, `tax_amount`, and `gross_amount`. Keep `unit_price` semantics explicit and invoice-format-specific. Arithmetic is allowed to detect a mismatch, never to fill or silently replace an absent/printed amount. Preserve the legacy `amount` field only with documented compatibility semantics; never guess historical semantics.
5. Every extracted field and relation should have provenance/evidence when available: source document/version/hash, page, raw quote, bounding box or OCR polygon, provider/model revision, extraction method and review state. Do not invent calibrated confidence. Distinguish model confidence from validation state.
6. Evidence checks must tolerate layout whitespace, punctuation, line wraps, Unicode, OCR substitutions and locale formatting. Store raw evidence and a separately computed match-normalized form/score. A mismatch between OCR text and a visual model is a review finding, not automatic proof the model is wrong. Validate geometry/page when returned. Never use exact substring matching as the only evidence check.
7. The model may return typed field/relation proposals only. Local code validates and writes graph data; never execute model-authored SQL/Cypher or let the model mutate storage.
8. Keep all extractions `needs_review` until an explicit reviewer approves them. Re-extraction must preserve past reviewed versions as superseded history; never erase review history implicitly. Support edges or ledger rows need their own source and review state so reviewed-only graph queries are possible.
9. Use training data only for any RAG corpus. Do not retrieve validation/test labels, and do not let similar invoice values overwrite evidence in the current image. RAG is a later, measured optional feature, not a required first slice.
10. Hosted calls must be disabled by default. No invoice is sent remotely unless an explicit environment opt-in is set, credentials are configured, and the user has accepted that data leaves the laptop. Never log keys or full document payloads. Keep providers replaceable, mock API calls in tests, set timeout/retry/page/pixel/concurrency/request-size limits, and record actual usage/cost metadata when available. Never send the sample or a real document in your own verification call without explicit instruction.
11. Keep CPU use bounded: single worker by default, per-page processing, no giant full-resolution image arrays retained, max pages/pixels/timeouts configurable, and a queue-only mode that loads no model. A model should load lazily only when selected.
12. SQLite remains the default until a measured requirement justifies a server. Use safe repeatable migrations, transactions/`BEGIN IMMEDIATE` or an equivalent single-writer design, WAL where appropriate, and preserve the last good graph on failed extraction.
13. No fine-tuning/foundation-model training until a measured baseline, data rights, hardware and labels justify it. The likely near-term path is a pretrained API model and/or compact OCR weights, not training a new model.

## Technical direction to validate

Use `TECH_RESEARCH.md` and verify current vendor docs before adding dependencies:

- Default runtime: remain queue-only unless an extraction provider is configured.
- Local option: compare Tesseract and a small RapidOCR/PP-OCR ONNX Runtime model. Keep OCR extraction (text, scores and boxes) separate from invoice interpretation. Avoid `PP-StructureV3` on the user's laptop unless measured memory/latency show it is safe.
- API option: implement one provider first behind a `VisionExtractionProvider` interface. Gemini's current docs describe native PDF/image input plus structured JSON; OpenAI and Anthropic also document vision-capable APIs. Choose one provider based on up-to-date model availability, output-schema support, price, regional availability, retention terms, and credential setup. Do not hard-code an unstable model ID; put it in config. Do not add all three SDKs at once.
- For API output, prefer provider-enforced JSON schema/structured outputs, but treat response as untrusted and validate locally. Save usage metadata and model revision. Retry only transient errors with bounded backoff; don't silently route local data to a different vendor.
- PDFs with a text layer should use local text extraction first; scanned pages use OCR or VLM. Images/PDF pages can be sent to the API only in the explicit remote mode.
- A 0.9B local vision-language model is still a VLM workload and may need a GPU-serving stack; do not equate parameter count with safe CPU performance. Do not use vLLM/CUDA unless the actual hardware supports it.

## Target module layout (adapt, do not force a big-bang migration)

Keep existing paths where possible; add modules in the current style first. This is a desired separation of responsibilities, not a requirement to rename everything immediately:

```text
PaperTrail/
├── AGENTS.md
├── README.md
├── .env.example                 # names only; never secrets
├── backend/
│   ├── requirements*.txt        # base and optional extras kept separate
│   ├── app/
│   │   ├── main.py              # queue/watcher entry point; provider selection
│   │   ├── config.py            # limits, local/API opt-in, paths
│   │   ├── ingestion/           # watcher, registry, stable-file/job handling
│   │   ├── extraction/
│   │   │   ├── providers.py     # provider contracts and result types
│   │   │   ├── schema.py        # canonical jurisdiction-neutral schema
│   │   │   ├── local_ocr.py     # light OCR adapter; lazy model load
│   │   │   ├── vision_api.py    # one opt-in provider; mocked in tests
│   │   │   ├── evidence.py     # raw quote, page, geometry and match diagnostics
│   │   │   ├── validation.py   # type, requiredness, arithmetic and review findings
│   │   │   └── evaluation.py   # labelled split evaluation, no train/test leakage
│   │   ├── graph.py             # compatibility facade initially
│   │   ├── graph_ontology.py    # allowed node/edge types and directions
│   │   ├── ledger.py            # source support, versions and superseded history
│   │   ├── pipeline.py          # job -> provider -> validation -> persistence -> graph
│   │   └── review/              # corrections/approvals and history (later milestone)
│   └── tests/
│       ├── fixtures/            # licensed/synthetic invoices and expected labels
│       ├── test_extraction.py
│       ├── test_graph.py
│       ├── test_pipeline.py
│       └── test_providers.py
├── data/README.md               # dataset instructions/licensing only
├── docs/
│   ├── SDD.md
│   ├── IMPLEMENTATION_GUIDE.md
│   ├── DATA_HANDLING.md
│   └── EVALUATION.md
└── reports/                     # ignored generated artifacts
```

Do not create a frontend until the repository/UI need is confirmed. No downloaded dataset, model weights, database, or credentials belong in version control or the handoff ZIP.

## Canonical output and validations

Define a stable typed document result. At minimum, preserve document metadata, invoice header, vendor/customer party objects, ordered line items, totals, findings, evidence, provider provenance, and review status. Each field evidence object should allow `raw_text`, typed `value`, page, source snippet, bounding polygon/box, method (`pdf_text`, `ocr`, `vision_model`, `human`), and an evidence-match diagnostic. Keep evidence optional when unavailable and report that limitation explicitly.

Line items need description, quantity, unit price, net amount, tax rate, tax amount, gross amount, row/page, and evidence per field. Handle decimal comma and locale settings without losing raw text. Do not assume net/gross meaning globally. For multi-page tables, preserve order and merge wrapped descriptions; flag repeated headers, page subtotals, `Balance b/f`, carried-forward lines, and duplicate row candidates instead of silently duplicating/omitting them.

Return explicit states: `valid_reconciled`, `valid_mismatch`, `incomplete`, `ambiguous_review`, and `invalid_response` (or a documented equivalent). Findings should say which field/row needs review and why. Check quantity × unit price against printed net, printed net plus printed tax against printed gross, item sums against subtotal/tax/total. Use decimal arithmetic and the invoice's rounding precision. These calculations only flag discrepancies; they never fill a missing extraction value.

Cross-check table row count using parser/table evidence where available; a count generated by the same VLM is not independent evidence. If document classification says `other`, retain it with a `document_type_unexpected` finding rather than silently dropping it.

## Knowledge graph and source ledger

Define a small domain ontology with explicit edge direction and stable IDs:

```text
(Document)-[:SUPPORTS {version, hash, review_status, evidence}]->(Invoice)
(Vendor)-[:ISSUED]->(Invoice)
(Invoice)-[:BILLED_TO]->(Customer)
(Invoice)-[:CONTAINS {row_index}]->(LineItem)
(LineItem)-[:CLASSIFIED_AS]->(ProductCategory)  # optional, only with supported classification
```

Only add `PurchaseOrder`, `Contract`, payment, or tax-regime nodes/edges when the invoice or linked evidence supports the relation. Store original display values and explicit match keys separately. Specify the match-key normalization (e.g. Unicode NFKC, casefold, collapsed whitespace; tax-ID-specific separators) and use exact match keys for merging. Never fuzzy-merge vendors/customers automatically. If two paths have identical bytes, document whether they represent two records or one content-addressed document; preserve distinct source paths when they are distinct business documents.

Implement the graph as a deterministic materialized view over source support. Maintain a ledger of source claims and support. The graph should equal the union of active supported facts; test this invariant. A new document version supersedes the old version while retaining its audit/review history. Deleting one source removes only its support; shared nodes/edges survive while any active support remains. Extraction, ledger diff, and graph sync should be transactional/idempotent. Do not merge unreviewed entity identities solely on a common vendor name/tax ID; carry `needs_review` on support and expose reviewed-only filtering.

## Staged milestones and gates

This is multi-week scope. Work one milestone at a time. **In the first Claude session, complete repository reconnaissance and Milestone 1 only, then stop and report.** Do not continue to API calls, UI, RAG, or full evaluation until the user reviews the deliverable.

### Milestone 0 — Audit (no broad edits)

Report actual folders/modules, actual graph schema/relations, provider/schema validation, migrations, test results if runnable, test fixture/license status, local hardware/runtime limits if known, data files present/missing, and differences from this prompt. Identify the smallest safe design. Never claim a machine/environment fact you did not inspect. If dependencies are unavailable, explain precisely.

### Milestone 1 — Data contracts, ledger, and deterministic graph (first implementation)

Use fake providers and fixtures only. Harden/add typed schema and migrations for field evidence/raw-vs-normalized values, extraction version history/superseded state, findings/review status, allowed graph node/edge types, and support ledger. Ensure idempotent rebuild from ledger, union invariant, transaction safety, and no deletion of shared facts. Ensure current graph retrieval remains backward compatible. Do not add API SDKs, heavy OCR packages, Neo4j, PostgreSQL, graph UI, or external calls in this milestone.

Required regression coverage: complete four-item invoice; multiline description; decimal comma; correct independent party names/addresses; tax fields; one missing item/no fabrication; wrong quantity; each arithmetic mismatch; null/malformed provider output; unknown historical `amount`; repeat reprocess; changed file; shared entity support; source deletion; superseded reviewed version; graph union invariant; duplicate identical bytes at two paths; repeated API/provider fixture. Add no test that needs a network/model.

Run unit tests after the milestone if the environment permits. Do not run an expensive OCR/model evaluation. Document any not-run command.

### Milestone 2 — Low-resource extraction adapters

Add a tiny/small RapidOCR ONNX or Tesseract adapter behind an optional dependency extra, lazy-loaded, one-page/one-worker CPU default, bounded image resize and timeout. Add one provider-neutral vision API adapter selected by configuration; choose the vendor/model only after checking current official docs, schema support, price and retention. API path is opt-in and disabled unless an explicit allow-remote setting and API key exist. Provide an explicit CLI for a single invoice and a clear cost/privacy notice. Mock both in tests; do not call real APIs unless the user explicitly asks. Preserve Donut baseline. Do not use PP-StructureV3 or local 0.9B VLM by default on the constrained laptop.

### Milestone 3 — Review and graph inspection surface

The repository currently has no frontend/API. First propose the smallest useful inspection surface. Implement only with a local authenticated/restricted interface appropriate to existing dependencies. Show original source, each field's raw text and parsed value, evidence/page/box, warnings, review state, graph node/edge type and support documents. Allow approval/correction without destroying extracted history. Keep local development secrets out of source.

### Milestone 4 — Reproducible evaluation and optional RAG

Establish current Donut baseline on all 50 validation rows, then compare local OCR+mapping and/or selected API provider on the identical rows with same canonical schema. Report per-field exact/normalized metrics, line-item precision/recall/F1, coverage, invalid response rate, complete-document accuracy, latency, token/cost usage, memory/CPU and confidence intervals appropriate to n=50. Save source image/row, provider/model revision, prompt/config, output, expected values and metrics in report. Only after a frozen validation improvement, run all 26 test rows once. No tuning or retrieval from validation/test labels. RAG may index training/approved examples only, with IDs logged and no example values used as current invoice values.

## Evaluation semantics and domain uncertainty

Your user's review of the prior prompt found these required fixes: preserve both `raw_text` and parsed `value`; separate display values from deterministic matching keys; carry review state on source support; retain superseded reviews; do not rely on exact OCR substring evidence; independently check table row counts; add arithmetic checks but never fill; specify classification fallback; handle multipage repeated headers/carry-forward rows; define duplicate-content identity; define SQLite write concurrency; and retire ambiguous `amount` semantics safely. Implement these in the relevant milestones.

The dataset is synthetic and primarily maps European invoice tax fields. Future real-world jurisdiction is unspecified. Do not assert Indian GST invoices are the target without user evidence. Keep jurisdiction-specific details extensible: tax ID type, tax components/rates, HSN/SAC, bank identifiers (IBAN, IFSC/account number), currency and locale number parsing. Only mark fields scoreable when the dataset labels them.

## Required completion report

At each milestone give: changed files; schema/migration effects; exact commands run and raw pass/fail counts; what was not run; measured results (never estimates as fact); model/runtime details and CPU/memory/cost observations if measured; unresolved issues; and the next milestone. Keep `README.md`, SDD, data notes and usage instructions consistent with implemented reality. Preserve user edits. Do not commit or push unless the user separately asks.

**First response/action:** do Milestone 0 audit; then implement Milestone 1 only if no architecture conflict requires user input. Stop after Milestone 1 with a reviewable patch and the report above.
