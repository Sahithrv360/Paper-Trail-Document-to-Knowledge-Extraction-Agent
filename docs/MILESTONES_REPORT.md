# Paper Trail: Milestones 1–4 Completion Report

Date: 24 October 2026. This is cumulative against the 24 October handoff ZIP. Milestone 1 (data contracts, ledger, deterministic graph) is described in `MILESTONE_1_REPORT.md`; this report covers Milestones 2–4 and the combined state.

## Summary

| Milestone | Status | Evidence |
|---|---|---|
| 1. Contracts, ledger, graph | Done | 55 tests; v1 database migration verified on a database written by the original code |
| 2. Low-resource adapters | Done | Real local OCR (Tesseract and RapidOCR) run on the fixture; vision API fully mocked; 28 adapter tests |
| 3. Review and graph surface | Done | Local server and page; 9 HTTP tests; checked in headless Chromium (desktop and phone width) |
| 4. Evaluation harness | Harness done; **dataset evaluation not run** | 8 tests; real Tesseract smoke run on synthetic copies of the fixture only |

**Not measured:** no accuracy claim is made. Hugging Face was unreachable from the build workspace, so neither the dataset nor the Donut checkpoint could be downloaded. Your computer had no connected folder containing the dataset. The validation comparison and the one-time test run therefore remain for you to run (commands below).

## Milestone 2: what was built

| File | Purpose |
|---|---|
| `backend/app/config.py` | `Settings.from_env()` with validated provider/parser choice, page, pixel, DPI, timeout and request limits, the remote opt-in (`PAPERTRAIL_ALLOW_REMOTE=I_ACCEPT_DOCUMENTS_LEAVE_THIS_DEVICE` plus key plus model) and a dependency-free `.env` loader |
| `extraction/document_input.py` | Bounded JPG/PNG loading; PDFs via optional pypdfium2 (text layer first, scanned pages rendered); page limit refuses rather than truncates |
| `extraction/local_ocr.py` | Tesseract subprocess (timeout, one thread) and RapidOCR in a killable child process, producing a `ParseContext` with boxes and an independent numbered-row count |
| `extraction/ocr_mapping.py` | Model-free mapper from labels and column geometry to invoice fields, each with OCR quote, page and box; ambiguity becomes a review reason |
| `extraction/vision_api.py` | Opt-in, vendor-neutral provider with an Anthropic Messages backend over stdlib HTTPS: strict JSON schema, untrusted-delimited OCR text, optional training-only RAG shapes, size limit, transient-only retries, usage and cost metadata, key never logged |
| `pipeline.py`, `main.py`, `reprocess.py`, `extract_one.py` | Document-aware jobs with permanent vs retryable error classes, scan thread plus one worker thread, recovery of interrupted jobs, single-file CLI with privacy/cost notice and `--dry-run` |
| `ingestion/registry.py` | `claimed_at` column (additive migration), `recover_interrupted_jobs()` |
| `extraction/validation.py` | IBAN checksum finding, `pages_not_processed`, nullable item descriptions |

**Measured on this 2-CPU Linux container (not your laptop), one A4 fixture page:**
- **Tesseract:** about 1.1–1.9 s per page, child process peak about 130 MB.
- **RapidOCR:** about 5–6 s, peak about 1.6 GB at full resolution.
- **What they read:** both read the header, both parties (as separate name and address), all 4 rows (wrapped descriptions included) and the totals. Tesseract read the IBAN as `GBO2…`; the new checksum check flags it, and the value is stored exactly as read.

**Fixes found while testing:**
- A garbled row number made the mapper drop a row. Rows with two or more amounts now start a line with a review reason.
- A mis-assigned summary amount when a header was missing: amounts must now sit under a labelled column.
- The fixture's hand label "Corlson" was a typo; the image says "Carlson", so the test data was corrected.

## Milestone 3: review surface

`python -m backend.app.review.server` prints `http://127.0.0.1:8765/#token=…`. It uses only the standard library and has no build step or remote assets.

**Security:**
- loopback bind only
- token header (from the URL fragment)
- `Host` and `Origin` checks
- JSON-only POST of at most 1 MB
- strict CSP
- source pages served only when the file hash matches the extraction under review
- optimistic concurrency on reviews

**The page:**
- source page with blue evidence boxes linked to field rows
- raw "as read" text beside the parsed value
- evidence status and findings
- approve/correct with reviewer name and note
- version and review history
- graph with a reviewed-only toggle and duplicate/identity suggestions
- change log

The headless-browser run covered the full flow: reviewer name required, correcting the misread IBAN, graph updated, reviewed-only view and change log. No console errors.

## Milestone 4: evaluation harness

`python -m backend.app.extraction.benchmark run|compare|freeze`:
- **Accuracy:** exact and normalized per-field accuracy with coverage, and whole-document accuracy.
- **Line items:** strict and normalized precision/recall/F1.
- **Quality and cost:** invalid response rate; review-flag quality (wrong but unflagged counted); latency, peak memory and tokens/cost.
- **Intervals:** 95% Wilson and bootstrap intervals; paired bootstrap comparison.
- **Test split:** guarded by a frozen fingerprint, all rows required, once per configuration.
- **RAG:** training-split only, API provider only.
- **Fairness fixes:** item matching uses printed gross amounts, and party labels are compared as combined name + address in the normalized metric.

## Commands run and raw results

| Command | Environment | Result |
|---|---|---|
| `python -m unittest discover -s backend/tests -v` | Python 3.13.17, Pillow, pypdfium2 5.13, rapidocr 1.2.3, Tesseract 5.3.4 | 147 run, 147 OK, 0 skipped |
| same | Python 3.12.3 (no pypdfium2) | 147 OK, 2 skipped |
| same | Python 3.11.17 (no Pillow, no pypdfium2) | 147 OK, 23 skipped (bare install works) |
| suite ×10, then timing-sensitive modules ×15 and review-server tests ×25 | Python 3.13 | One intermittent failure found (413 broken-pipe race), fixed; 0/25 after the fix |
| `python -m pyflakes backend/app backend/tests` | | Only the 2 pre-existing warnings in untouched files |
| `python -m backend.app.extract_one fixture --provider ocr` | Tesseract | `ambiguous_review` (IBAN checksum); parse 1.19 s, extract 0.28 s |
| `extract_one … --provider vision-api --dry-run` | no key or opt-in | Would send 1 page, 255,173 bytes; lists 3 unmet conditions; nothing sent |
| `benchmark run --provider ocr --parser tesseract` on 3 synthetic copies of the fixture | Tesseract | Harness check only: normalized micro field 93.8%, IBAN 0/3 (all flagged), line items F1 100%, 1.86 s/page. **Not an accuracy result.** |

## Not run

- Donut, OCR or API evaluation on the dataset's validation and test splits (dataset unavailable).
- Any real vision-API call (no key; the gate forbids sending documents without your explicit opt-in).
- Anything on your Windows laptop. Windows-specific behaviour (Tesseract path, `psutil` memory figures, spawn for RapidOCR) follows documented paths but was not exercised.

## To run the evaluation yourself

```powershell
pip install -r backend/requirements-extraction.txt -r backend/requirements-ocr.txt -r backend/requirements-pdf.txt
python -m backend.app.extraction.benchmark run --split validation --provider donut
python -m backend.app.extraction.benchmark run --split validation --provider ocr --parser tesseract
python -m backend.app.extraction.benchmark compare reports/bench-validation-donut.json reports/bench-validation-ocr.json
# choose one configuration, then (once):
python -m backend.app.extraction.benchmark freeze reports/bench-validation-ocr.json
python -m backend.app.extraction.benchmark run --split test --provider ocr --parser tesseract --final-test-run --frozen reports/frozen-ocr-layout.json
```

## Unresolved issues and risks

1. **Layout rules:** the OCR mapper's rules were written against one template family (the fixture plus synthetic layouts). Expect weaker results on other layouts; the review flags are the safety net.
2. **Unread text:** OCR misses (for example a dropped digit at low resolution) surface as missing-field or checksum findings, but cannot be recovered without the image. `MAX_PAGE_PIXELS` trades memory for accuracy.
3. **Numbering across pages:** the independent row count is only reported for single-page documents.
4. **Purging:** there is no selective purge of stored history (by design, for audit); delete the databases to purge.
5. **Vendor choice:** the API backend is Anthropic, chosen by Claude. Check vendor terms and pricing and consider alternatives; the provider interface allows another backend.
6. **Older items, unchanged:** the inherited `"1,234"` grouping ambiguity, and the PaddleOCR PP-StructureV3 preview status on Windows.
