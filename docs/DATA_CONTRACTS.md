# Data Contracts, Source Ledger and Graph

**Status:** Implemented in Milestone 1 (fake/replayed providers only). **Date:** 2026-10-24.

This document is the reference for the canonical extraction record, field evidence, review findings, the source ledger and the deterministic graph. It describes implemented behaviour; nothing here reflects a measured model accuracy.

## 1. Canonical extraction record

`extract_validated()` (`backend/app/extraction/providers.py`) is the single place where provider output becomes a stored record. Output from every provider (Donut, a future OCR or API adapter, the test `FakeProvider`) goes through the same schema checks, evidence collection and validation.

| Key | Meaning |
|---|---|
| `invoice_number`, `invoice_date`, `currency`, `bank_account_iban` | Strings exactly as reported. Dates and identifiers are never reformatted. |
| `vendor`, `customer` | Separate party objects (`name`, `address`, `tax_id`; customer also `phone`, `email`). Data never moves between parties. An address is only separated when the provider separated it. |
| `items[]` | Ordered rows: `description`, `quantity`, `unit_price`, `net_amount`, `vat_amount`, `gross_amount`, `tax_rate_percent`, `page`, `source_row`, plus deprecated `amount`/`amount_semantics`, `field_origins`, `derived`. |
| `subtotal`, `tax_amount`, `total_amount` | Parsed non-negative numbers or `null`. |
| `doc_type` | Provider classification in snake case (`invoice`, `credit_note`, `purchase_order`, ...); `null` means the provider did not classify. |
| `row_count_seen` | The model's own row count. Not independent evidence. |
| `extensions` | Jurisdiction-specific raw scalars (GSTIN, HSN/SAC, IFSC, ...). Kept for review only: not validated, scored or written to the graph. |
| `field_evidence` | Per-field source reading and evidence (section 2). |
| `findings` | Structured review findings (section 3). |
| `validation_status`, `review_reasons` | Status (section 3) and human-readable reasons. |
| `review_status` | Always `needs_review` for machine output. Only a human review changes it. |
| `provenance` | Document ID, version, hash, source filename, provider, model revision, `prompt_version`, `extraction_method`, parser summary, raw model response and generation diagnostics. |

Numbers are parsed with `parse_amount`. Currency labels around a number are ignored (`"$ 7.00"`, `"7,00 EUR"`). Any other character inside the digit run makes the value unreadable (`"69A,9O"` becomes `null` with an `unparseable_value` finding). Negative values are rejected. Decimal comma and grouping marks are handled (`"1.234,56"`, `"1 234,56"`). A single comma followed by exactly three digits is still read as grouping (`"1,234"` → 1234); this is an inherited ambiguity.

**No arithmetic filling.** A value that is not printed stays `null`. For the Donut dataset, which has no line-VAT label, `gross − net` is kept under `items[i].derived.vat_amount` with `"use": "validation_only"`. It is never stored as `vat_amount`, and evaluation does not score unlabelled item fields.

## 2. Field evidence

`record["field_evidence"]` maps a field path to an evidence object. Paths are `invoice_number`, `vendor.name`, `items[0].quantity` (a zero-based index into `items`). Providers may also send `items.0.quantity`.

```json
{
  "raw_text": "125,00",
  "raw_type": "str",
  "value": 125.0,
  "page": 1,
  "quote": "125,00",
  "bbox": [412, 640, 470, 656],
  "method": "vision_model",
  "match": {"source": {"status": "exact", "score": 1.0},
            "value_in_quote": true, "quote_form": "12500"}
}
```

- `raw_text` is the provider's reading as reported. When a provider returns a JSON number, `raw_text` is `null` and `raw_type` is `"float"`/`"int"`; printed formatting is never reconstructed. Reviewers should be shown `raw_text`.
- `method` is one of `pdf_text`, `ocr`, `vision_model` or `human`.
- `match.source.status` compares the quote with parser/OCR text (`ParseContext`). The forms are tried in order: `exact`, then `normalized` (NFKC, casefold, alphanumerics only, which tolerates whitespace, punctuation, line wraps and locale marks), then `ocr_folded` (o/0, i/l/1, s/5, z/2, b/8, g/9), then `fuzzy` (best window similarity ≥ 0.85), and finally `not_found`. Quotes of one or two characters return `too_short`. `no_quote` and `no_source_text` mean the comparison could not run.
- A failed match is a review finding, never proof that the model is wrong (OCR may be the side that misread), and never a correction.
- Page numbers must be positive and within the parsed page count. A `bbox` may be `[x0, y0, x1, y1]` or a polygon, in pixels or 0–1 units, and must lie within the page when the page size is known.

## 3. Findings and validation status

Each finding is `{code, effect, field, row, message, details?}`. `row` is 1-based. The effect of each code is fixed in `validation.CODE_EFFECTS`.

| Effect | Codes |
|---|---|
| `incomplete_rows` | `generation_truncated`, `row_count_mismatch` (parser table count ≠ rows), `row_count_self_report_mismatch` |
| `mismatch` | `arithmetic_line_net`, `arithmetic_line_tax`, `arithmetic_line_tax_from_gross`, `arithmetic_line_gross`, `arithmetic_sum_net`, `arithmetic_sum_tax`, `arithmetic_sum_gross`, `arithmetic_invoice_total` |
| `incomplete` | `missing_field`, `missing_party`, `no_line_items`, `item_missing_field` |
| `ambiguous` | `party_address_unseparated`, `document_type_unexpected`, `repeated_header_row`, `carried_forward_row`, `page_subtotal_row`, `duplicate_row_candidate`, `possible_wrapped_description`, `evidence_not_in_source`, `value_not_in_quote`, `evidence_page_invalid`, `evidence_geometry_invalid`, `unparseable_value`, `provider_review_reason` |
| `none` (informational) | `row_count_not_independent`, `legacy_amount_semantics_unknown`, `evidence_missing` |

Added in Milestone 2: `pages_not_processed` (`incomplete_rows`; an image-only provider received a multi-page document) and `iban_checksum_invalid` (`ambiguous`; ISO 13616 structure/mod-97 failure, stored as read). `item_missing_field` now also covers a null `description`: an unreadable description is kept as `null` instead of rejecting the row. `ParseContext.table_row_method` records how the parser count was obtained (`numbered_rows_heuristic`). Multi-line quotes that fail as a whole are matched line by line (status `lines`).

The status is decided by the highest-precedence effect present:

| Stored status | Build-prompt name | Rule |
|---|---|---|
| `incomplete_extraction` | `incomplete` | any `incomplete_rows`, else (after mismatch) any `incomplete` |
| `arithmetic_mismatch` | `valid_mismatch` | any `mismatch` (ranks above plain missing fields) |
| `ambiguous_review` | `ambiguous_review` | any `ambiguous` |
| `valid_reconciled` | `valid_reconciled` | no status-affecting findings |
| `invalid_response` | `invalid_response` | job-level only; output unparsable, nothing stored |

Arithmetic uses `Decimal` and the printed precision: the most decimals seen in money `raw_text`, defaulting to 2. The tolerance is two units in the last printed place (0.02 for cents). Sums are only checked when rows exist. Multi-page artefacts (repeated headers, `Balance b/f`, page subtotals, exact duplicate rows, text-only wrapped rows) are flagged and never dropped or merged.

## 4. Entity identity

`backend/app/extraction/matching.py`:

- `name_key`: Unicode NFKC, casefold, collapsed whitespace; punctuation kept.
- `tax_id_key`: NFKC, uppercase, whitespace and `- . / _` removed.
- `party_match_key = "mk1|name=<name_key>|tax=<tax_id_key or ''>"`. Vendor and Customer nodes merge only when both parts match exactly.

This is deliberately conservative. A missing tax ID on one invoice produces a separate node instead of a guessed merge. `GraphStore.entity_suggestions()` lists `same_name_different_tax_id` and `same_tax_id_different_name` pairs for a reviewer; nothing is merged automatically. Displayed values stay as extracted. A shared node takes its display properties from its lowest document ID, so the result does not depend on processing order, and other spellings are listed in `variants`.

Identical bytes at two paths are **two documents**, because the registry identifies documents by path. Each has its own Document and Invoice nodes, and both support any shared party. `GraphStore.duplicate_content_groups()` reports them. A moved file keeps its document ID when its content matches exactly one deleted document (existing registry behaviour).

## 5. Ontology

`backend/app/graph_ontology.py` defines the allowed types and edge directions; violations raise `OntologyError`.

```text
(Document)-[:SUPPORTS {version, content_hash, review_status}]->(Invoice)
(Vendor)-[:ISSUED]->(Invoice)
(Invoice)-[:BILLED_TO]->(Customer)
(Invoice)-[:CONTAINS {row_index}]->(LineItem)
(LineItem)-[:CLASSIFIED_AS]->(ProductCategory)   # defined, never emitted yet
```

Stable IDs: `Document:<document_id>`, `Invoice:<document_id>`, `LineItem:<document_id>:<row_index>`, `Vendor:<sha256(match_key)[:16]>`, `Customer:<...>`. A document whose `doc_type` is not `invoice` contributes only its Document node.

## 6. Source ledger (graph database)

| Table | Contents |
|---|---|
| `extraction_versions` | Every accepted machine extraction (immutable `record`), `state` active/superseded/removed (one active per document), `superseded_by`, review status, provider/model/prompt version, `run_id` (for example `job:12`). |
| `reviews` | Append-only review history: decision, reviewer, note, corrected record, `state` active/superseded. |
| `claims` | Per-document support for each node/edge: properties, match key, evidence, review status, `active`. Old claims are deactivated, never deleted. |
| `nodes`, `edges` | Materialized union of active claims. |
| `graph_changes` | One row per graph-affecting write (`create`, `update`, `unchanged`, `delete`, `review`, `rebuild`, `migrate`), with added/updated/removed node and edge IDs computed by diffing inside the same transaction. |

Update rules:

- **Same document, same hash:** no-op (no ledger row).
- **Explicit same-version reprocess** (`allow_same_version_replace`) with an identical record: one `unchanged` ledger row and no new version.
- **New hash:** the previous extraction becomes `superseded` and keeps its reviews; only that document's claims are replaced. A reviewed active extraction blocks replacement unless `allow_reviewed_replace=True`; the pipeline then fails the job permanently and the reviewed graph stays.
- **Delete:** the extraction becomes `removed`, its claims are deactivated, and shared nodes survive while any active claim supports them.
- **Failure:** every write is one `BEGIN IMMEDIATE` transaction; an exception leaves extraction, claims, graph and ledger untouched (tested by fault injection).
- **Invariant:** `nodes`/`edges` equal the union of active claims of non-removed documents. `rebuild_graph()` recomputes from claims and reports any drift (expected empty). A randomized test checks this after every operation.

Reviews (`apply_review(document_id, decision, corrected_record=None, reviewer=None, note=None)`):

- `corrected` validates the reviewer's full record and stores it beside the machine record. Unchanged fields keep the machine evidence; changed fields get `method: "human"` evidence.
- `approved` accepts the current effective record, including an earlier active correction.
- A newer review supersedes the previous one but keeps it in history. Claims are re-derived with the reviewed status in the same transaction.
- `export(reviewed_only=True)` builds the graph from reviewed support only, so facts and merges asserted only by unreviewed extractions are excluded.
- `mark_reviewed()` remains as a compatibility wrapper.

Other reads: `get_extraction()` returns the machine `record`, the active `review`, the `effective_record` and the version. `extraction_history()`, `synced_documents()` (every file known to the graph with its latest hash, version, state, review status and last sync time) and `changes()` are also available.

**Concurrency:** both SQLite databases use WAL. Graph writes take `BEGIN IMMEDIATE` with a 30-second busy timeout, so concurrent writers serialize (tested with eight threads).

**Migration:** `PRAGMA user_version = 2`. A version-0 graph database from the original store is upgraded in one transaction. `extractions`, `support` and `synced` are renamed to `legacy_v1_*` (not dropped), each stored extraction is imported as an active version, and approved/corrected records get a `legacy-import` review row (corrected values were never recorded by v1). The graph is then rebuilt from claims and a `migrate` ledger row is written. Re-running is a no-op. Node IDs for `Invoice` and party nodes differ from v1.
