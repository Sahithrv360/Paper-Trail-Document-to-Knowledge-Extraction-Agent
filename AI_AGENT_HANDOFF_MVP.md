# Paper Trail - Handoff for the Next AI Agent

**Updated:** 24 October 2026
**Workspace:** `C:\Users\sahit\OneDrive\Desktop\chintu\Cadra Training\project main`
**Purpose:** Continue this repository toward a tested, evidence-backed invoice-to-knowledge-graph MVP. Read this file with `AGENTS.md`, `CODEX.md`, `APPROACH.md`, `docs/build-plan.md`, and `docs/SDD.md`.

## User's MVP Goal

Build a local invoice workflow that watches a folder, extracts invoice fields accurately without making the laptop hang, shows the source file and evidence for each extracted value, validates amounts, and creates a persistent graph with correct typed relationships. The user wants to test accuracy later and is considering a vision API because local PaddleOCR was too slow. Keep API usage opt-in and measurable. Do not send private invoices externally without the user's explicit approval.

The first MVP should focus on invoices. Contracts, enterprise integrations, production deployment, and broad RAG are later work. The target jurisdiction for future real invoices has not been confirmed; do not assume India or Europe.

## Current Repository and Implemented Work

- **Ingestion:** `backend/app/ingestion/` contains a folder watcher and SQLite document/job registry. It handles stable-file scanning, hashes, versions, retry state, moves, deletion, and startup scans.
- **Extraction baseline:** `backend/app/extraction/donut.py` and `providers.py` wrap the pretrained `katanaml-org/invoices-donut-model-v1`. Donut is an existing baseline, not a new model. `EXTRACTION_PROVIDER=none` is queue-only; `donut` enables local JPG/PNG extraction.
- **Schema and validation:** `schema.py` maps dataset labels to canonical fields, preserves unavailable values as null, distinguishes net/VAT/gross amounts, retains the legacy `amount` field with explicit/unknown semantics, and checks arithmetic without overwriting printed values. Provider output goes through shared validation and is kept `needs_review`.
- **Dataset/evaluation:** dataset helpers use `katanaml-org/invoices-donut-data-v1` (425 train, 50 validation, 26 test). `data/README.md` records dataset/source licensing and caveats. Historical five-row test result: 28/75 exact field matches (37.3%) and 0/5 fully exact documents. This is a small compatibility baseline, not proof of general accuracy. Donut and dataset share provenance. Do not tune on the test split.
- **Knowledge graph:** `backend/app/graph.py` persists SQLite nodes and edges for Document, Invoice, Vendor, Customer, and LineItem, with `SUPPORTS`, `ISSUED`, `BILLED_TO`, and `CONTAINS`. It tracks document support, is idempotent for the same version, protects reviewed records from unattended replacement, and exports JSON. `pipeline.py` connects queued image jobs to validation and graph sync. There is no graph API or live UI.
- **Milestones 2–4:** bounded image/PDF loading, Tesseract and optional RapidOCR adapters, OCR-to-field mapping with quotes/boxes, an explicitly opt-in Anthropic vision provider, a local loopback review page, interrupted-job recovery, and a validation-first benchmark harness are implemented. See `docs/IMPLEMENTATION_GUIDE.md`, `docs/DATA_HANDLING.md`, and `docs/EVALUATION.md`.
- **RAG:** `rag.py` only retrieves training-split format patterns; `compare_rag.py` exists, but Donut ignores text prompts, so its RAG arm is a no-op control. No RAG benefit has been measured.
- **Evidence and validation (Milestone 1):** `extraction/evidence.py`, `matching.py`, and `validation.py` retain raw text and parsed values, tolerate common OCR/layout differences in evidence checks, validate geometry, and report structured arithmetic/row-count/classification findings. Checks flag discrepancies and do not fill or rewrite values. Donut has no parser wired in, so its live records can still report missing evidence.
- **Ledger and graph (Milestone 1):** `backend/app/ledger.py` stores extraction versions, review history, evidence-backed claims, and graph changes. `graph_ontology.py` limits allowed graph types and directions. Graph schema v2 migrates v1 data while retaining legacy tables; graph facts are a deterministic projection of active document claims. Re-sync is idempotent, reviewed-only exports are supported, and registry/graph SQLite use WAL.
- **Regression coverage:** `backend/tests/` includes the `invoice-242.jpg` fixture and tests for extraction contracts, adapters, evidence, amount validation, graph/ledger invariants, migration, review routes, and benchmark metrics. The full suite in this workspace passed **147 tests with 2 optional Tesseract tests skipped**. This does not establish dataset accuracy.
- **Documentation/prototype:** `APPROACH.md`, `CODEX.md`, `docs/build-plan.md`, `docs/SDD.md`, `docs/IMPLEMENTATION_GUIDE.md`, and `docs/prototype.html` describe approach, implementation status, build phases, and a static reviewer flow. The prototype is not connected to the backend. `pics/` contains project/gate screenshots; inspect for personal information before sharing.

- **Evaluation status:** `backend/app/extraction/benchmark.py` can compare providers on identical validation rows and enforces a frozen, one-time test configuration. The dataset and Donut checkpoint were unavailable when the milestone report was written, so no OCR/API validation comparison or final test result exists. Historical Donut five-row baseline: 28/75 exact fields (37.3%), 0/5 exact documents.

## PaddleOCR Failure and Model Choice

`backend/app/extraction/paddle_ocr.py` is a standalone PP-StructureV3 OCR preview, not a provider in the watcher and not invoice-field extraction. The user's Windows output showed PP-StructureV3 loading multiple layout, OCR, and table models; an earlier run failed in Paddle's oneDNN CPU path. Code was changed to disable MKL-DNN on CPU. In the next user run, PowerShell returned without a success summary or visible traceback; the exit code and JSON output were not confirmed. Treat PaddleOCR as unresolved and do not enable this heavy pipeline in the watcher.

`backend/requirements-paddleocr.txt` is an optional extra. Do not reinstall or rerun the PP-StructureV3 preview on the laptop as a default next step. See `TECH_RESEARCH.md` and `CLAUDE_BUILD_PROMPT_V2.md` for evaluated options. The preferred direction is:

1. For local CPU use, compare a small RapidOCR/PP-OCR ONNX Runtime model with Tesseract, processing one page/document at a time with limits.
2. For structured image/PDF extraction on a constrained laptop, implement one configurable, opt-in vision API provider behind the existing provider interface. Select the vendor/model only after checking its current official docs, output-schema support, price, availability, retention policy, and regional constraints.
3. Keep Donut as baseline. Mock providers in tests. Do not make real API calls or transmit the fixture/documents unless the user explicitly asks.

The Anthropic vision API adapter is implemented, but it is not enabled by default. Actual sending requires `PAPERTRAIL_ALLOW_REMOTE=I_ACCEPT_DOCUMENTS_LEAVE_THIS_DEVICE`, a key, and a model ID. Tests mock the transport; do not send documents without the user's explicit approval. A vision API calls a pretrained model; it does not train a new model from scratch.

## Main Gaps Before MVP

1. The labeled dataset evaluation has not been run with the new OCR/provider paths. Accuracy gains remain unverified; use validation first and reserve test data for a frozen final run.
2. OCR mapping uses heuristic labels and geometry tuned on a narrow template family. Review ambiguous rows and validate against more invoice layouts.
3. No runtime or memory measurements have been made on the user's Windows laptop. Tesseract integration tests were skipped in this environment because Tesseract is not installed; RapidOCR may use substantial memory.
4. The Anthropic vision API has only mocked tests. It requires explicit opt-in and real credentials; vendor price, retention, regional terms, and model availability must be checked before any live call.
5. The independent item row count is available only for supported single-page tables. Multi-page continuation rows and jurisdiction-specific fields need broader fixtures and labels.
6. The inherited `parse_amount("1,234")` locale ambiguity remains. GSTIN/HSN/SAC/IFSC and other jurisdiction-specific data are retained as unvalidated extensions; the target jurisdiction is unknown.
7. Product taxonomy/master-data integrations and broader graph/RAG use remain out of scope. RAG is available only to the vision API adapter and has not been evaluated.
8. Review UI is local-only. Add tests and review before exposing it beyond loopback; design a selective purge policy if audit-history deletion is required.

## Required Extraction and Graph Rules

- Preserve observed strings as `raw_text`; store parsed values separately. Keep match-normalized keys distinct from displayed names/tax IDs.
- Every field/relation should carry document ID, hash/version, page, source text, OCR polygon or box, provider/model and method where available. Do not invent confidence. OCR text mismatch is a review finding, not automatic proof that the visual model is wrong.
- Keep vendor and customer fields separate. Keep IBAN/IFSC outside party addresses. Do not infer currency or country-specific tax fields without evidence.
- Model output is untrusted data, not graph instructions. Validate locally; create graph nodes/edges deterministically from allowed types and evidence-backed records.
- Preserve all extractions as `needs_review`. Store review state on source support. A new hash supersedes old data but retains its review history. Deleting one document removes only its support; shared facts remain while supported elsewhere.
- Validate every printed row, wrapped descriptions, decimal-comma notation, table row count from independent parser evidence, and repeated headers/carry-forward rows. Never fabricate rows/quantities/amounts. Arithmetic may flag discrepancies but must not fill missing values.
- Use exact documented matching keys; do not fuzzy-merge vendors/customers automatically. Define duplicate-content behavior when identical bytes exist at different paths.
- Dataset labels combine seller/client name and address; don't pretend separated addresses are labelled ground truth. The dataset is synthetic and has a European VAT schema. The supplied `$` fixture does not establish the target country.

## Recommended Build Sequence

Follow the gates in `CLAUDE_BUILD_PROMPT_V2.md`; do one phase at a time and report before proceeding.

1. **Milestones 1–4 — implemented:** contracts/ledger/graph, bounded extraction adapters, review surface, and evaluation harness are present. The 147-test suite passes locally (2 Tesseract tests skipped); see `docs/MILESTONES_REPORT.md`.
2. **Next: local validation run.** Confirm Tesseract is available (`tesseract --version`), install `backend/requirements-ocr.txt` and `backend/requirements-pdf.txt`, then run `python -m backend.app.extract_one backend/tests/fixtures/invoice-242.jpg --provider ocr`. Inspect the `needs_review` result and review page before using the watcher.
3. **Measure extraction accuracy.** Download the labeled dataset using `data/README.md`; run the benchmark on validation rows for Donut and OCR, compare identical examples, and inspect per-field/line-item errors. Do not run or tune against the test split until settings are frozen.
4. **Optional hosted model.** First verify current Anthropic model terms and pricing. Start with `--dry-run`; only enable the exact remote acceptance phrase and send real documents after explicit user approval.
5. **MVP hardening:** test on Windows hardware; broaden invoice templates and multi-page fixtures; resolve locale ambiguity and target jurisdiction; improve OCR row mapping; add a selective audit-history purge policy if needed.

Continue one milestone at a time. Do not claim improved extraction accuracy until the labeled validation benchmark demonstrates it.

## Commands and Environment Notes

Run these from the repository root in the user's PowerShell:

```powershell
python -m unittest discover -s backend/tests -v
python -m backend.app.main
```

The watcher defaults to queue-only. OCR uses Tesseract (`EXTRACTION_PROVIDER=ocr`); install its binary and optional Python requirements. Donut needs `backend/requirements-extraction.txt` and a large model download. Keep dataset inputs under ignored `data/raw/`, separate from `watched/`. Avoid PP-StructureV3 on this laptop. Prefer the 50-row validation split for experiments and reserve all 26 test rows for the guarded final evaluation.

## Cadra Trace Plugin: Connection and Submission

The workspace is connected to Cadra. The Cadra Trace Tracker plugin/skills are installed under:

`C:\Users\sahit\.codex\plugins\cache\cadra\cadra-trace-tracker\3.1.0\`

The workspace ignores `.cadra/`; never copy, open, or share its config/token. Never ask the user to paste a token into chat. Start future coding-agent sessions from this repository root so Cadra can discover them.

Cadra workflow from the skill:

1. Run the preview in PowerShell (from any directory):

   ```powershell
   python "C:\Users\sahit\.codex\plugins\cache\cadra\cadra-trace-tracker\3.1.0\scripts\cadra_submit.py" --workspace "C:\Users\sahit\OneDrive\Desktop\chintu\Cadra Training\project main" --dry-run
   ```

2. Show the user session IDs, turn/message/byte/redaction counts, and every CWD. Check if any path looks private or unrelated.
3. Obtain user confirmation for that exact preview.
4. After approval, run the same command without `--dry-run`, then report only the confirmed `SUBMITTED`/`SKIP` receipt and excluded `NOTE` reasons.

The latest successful submission on 24 October 2026 sent the changed session `01a14d9e` (59 user turns; 1,054 messages) and Cadra marked `01a16d52` and `01a16d65` already submitted and unchanged. Three other records were excluded because they started in other directories; `opencode.db` could not be read. If submitting again, preview afresh; unchanged sessions will be skipped. Do not include Cadra tokens or `.cadra/last-preview.json` in project handoffs.

## Handoff Files to Read and Share

- `AI_AGENT_HANDOFF_MVP.md` - this current summary.
- `CLAUDE_BUILD_PROMPT_V2.md` - staged implementation prompt, corrections from the prompt review, schema rules, target layout, acceptance coverage.
- `TECH_RESEARCH.md` - researched local/API OCR and VLM options, with official documentation links.
- `PaperTrail-Claude-Handoff-2026-10-24.zip` - previous code/report/prompt package. It was prepared before the latest Gate 1 artifacts and `pics/` were added; share the current repository or supplement that ZIP with `APPROACH.md`, `CODEX.md`, `docs/build-plan.md`, `docs/prototype.html`, `docs/approach-build-transcript.md`, and any screenshots after reviewing them.

Never include `.git`, `.venv`, `.cadra`, credentials, databases, the full downloaded dataset, model caches, private documents, or arbitrary contents of `watched/` in a handoff archive.

## Working Tree Note

At the time of this handoff, some changes are local and uncommitted: `.gitignore`, `README.md`, `docs/IMPLEMENTATION_GUIDE.md`, `docs/SDD.md`; new files include this handoff, `CLAUDE_BUILD_PROMPT_V2.md`, `TECH_RESEARCH.md`, `backend/app/extraction/paddle_ocr.py`, `backend/requirements-paddleocr.txt`, and `pics/`. Run `git status --short` before editing and preserve these. The current handoff ZIP predates some of these items. No unit or OCR tests were run while writing this handoff.
