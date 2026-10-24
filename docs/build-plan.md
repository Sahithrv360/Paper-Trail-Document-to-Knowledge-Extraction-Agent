# Paper Trail Build Plan

This plan is sequenced around a small, reviewable invoice workflow. It reuses the existing watcher, Donut baseline, schema, SQLite graph, and evaluation code; it does not propose rebuilding a data warehouse. Status reflects the repository on 24 October 2026.

## Delivery sequence

| Phase | Work | Exit evidence | Status |
|---|---|---|---|
| 1. Local ingestion foundation | Watch a configured folder, validate files, track content versions and jobs, and make retries/deletions idempotent. | Unit tests for watcher and registry behaviour. | Implemented for local files; PDFs are queued but cannot be extracted. |
| 2. Invoice extraction baseline | Run the existing pretrained Donut adapter on supported images, normalize into the canonical schema, and retain provider/model provenance. | Reproducible labelled-set report with field-level comparisons. | Implemented. The recorded five-row test run was 28/75 exact fields (37.3%) and 0/5 exact documents; this small sample is a baseline only. |
| 3. Validation and persistence | Validate schema and arithmetic, keep every result in `needs_review`, then persist document-supported facts in SQLite. | Unit tests for validation and graph sync; inspection command shows stored extraction and warnings. | Implemented for image jobs. No extraction is auto-approved. |
| 4. Evidence-capable document path | Add page-aware PDF text extraction and OCR fallback; preserve text spans/coordinates through canonical fields. Connect OCR output to extraction instead of treating OCR preview as field extraction. | Labelled validation examples verify source/page references and OCR-to-field mapping, including unreadable-input handling. | Next technical milestone. Current PaddleOCR command is a standalone preview only; Donut does not return source evidence. |
| 5. Conservative entity resolution | Generate candidates from an approved master-record source; use deterministic identifiers/attributes where available, and abstain when evidence is weak or conflicting. | Ambiguous-name cases show candidate reasons and remain unresolved unless supported. | Planned; no master-data connector or resolution workflow exists. |
| 6. Reviewer experience | Build a document queue/detail view showing extracted facts, source evidence, warnings, candidate matches, and review status; record approve/correct/reject decisions with provenance. | Prototype flow reviewed, then connected to an API/store; reviewer actions are auditable and protected from silent overwrite. | Static UX sketch only; no frontend or review API exists. |
| 7. Evaluation and iteration | Create versioned labelled cases for invoices, evidence, ambiguity, and conditional contract language. Compare changes on validation data; reserve test data for final reporting. | Report sample counts, configuration, per-field quality, evidence support, abstention, latency/failures, and limitations. | Baseline evaluator exists for the public invoice dataset; target-specific evaluation remains to be built. |

## Build principles

1. Keep document text, extracted mentions, and resolved master entities distinct. Never invent a master identifier.
2. Require evidence references for claims in the target workflow; schema validity alone is not evidence of truth.
3. Keep uncertain, unsupported, conflicting, and failed cases visible for review. Do not auto-approve model output.
4. Use deterministic code for file safety, normalization, schema checks, persistence, and metric calculation. Keep the extraction provider replaceable.
5. Use synthetic material for product development. Do not transmit sensitive documents to hosted services without approval.
6. Make a measured validation comparison before changing models or adding retrieval. Do not claim quality gains from a no-op retrieval path or OCR confidence scores.

## First build increment

The next useful increment is a page-aware OCR/text adapter that maps text into the canonical invoice schema and attaches source references. Run it against labelled validation images/PDFs, compare it with the Donut baseline, then expose the stored extraction through a minimal read-only review screen. Add editable reviewer decisions only after evidence display and persistence are reliable.

## UX prototype

The static sketch in [`prototype.html`](prototype.html) illustrates the intended reviewer journey with a queue, extracted fields, evidence, and an unresolved vendor match. It contains sample content only and is not connected to project data or an API.
