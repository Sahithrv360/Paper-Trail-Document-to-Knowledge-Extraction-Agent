# Evaluation

**Status (2026-10-24):** the harness is implemented and tested. **No new accuracy result has been measured** on the dataset. The workspace used to build Milestones 2–4 could not reach Hugging Face, and the local dataset was not available. The only measured model result remains the historical Donut run (five test rows: 28/75 exact fields, 0/5 documents).

## Commands

Download the dataset into `data/raw/invoices-donut-data-v1/{train,validation,test}/` (see `data/README.md`). Then, from the repository root:

```powershell
# 1. Baselines and candidates on the SAME 50 validation rows
python -m backend.app.extraction.benchmark run --split validation --provider donut
python -m backend.app.extraction.benchmark run --split validation --provider ocr --parser tesseract
python -m backend.app.extraction.benchmark run --split validation --provider ocr --parser rapidocr --output reports/bench-validation-ocr-rapid.json
#    optional, billed, sends images (needs the remote opt-in, see DATA_HANDLING.md):
python -m backend.app.extraction.benchmark run --split validation --provider vision-api --parser tesseract

# 2. Paired comparison (B minus A, paired bootstrap over documents)
python -m backend.app.extraction.benchmark compare reports/bench-validation-donut.json reports/bench-validation-ocr.json

# 3. Only after choosing and freezing a configuration on validation:
python -m backend.app.extraction.benchmark freeze reports/bench-validation-ocr.json
python -m backend.app.extraction.benchmark run --split test --provider ocr --parser tesseract --final-test-run --frozen reports/frozen-ocr-layout.json
```

Each run writes `reports/bench-<split>-<provider>.json` (every document's source row, output record, expected values, per-field matches, findings, timing and usage) and a Markdown summary next to it.

Your own labelled real invoices use the same manifest format: `data/real/validation/manifest.json` with `{row_index, file, ground_truth}` entries, where `ground_truth` is a `gt_parse` JSON string. Run them with `--data-root data/real`.

## Metrics

| Metric | Definition |
|---|---|
| Field accuracy, exact | The historical comparison (`evaluation.py`): whitespace and case insensitive for text, numeric equality for numbers. Includes the legacy `vendor`/`customer` (name only) and `line_item_amounts` fields so it stays comparable with old reports. |
| Field accuracy, normalized | Text compared as NFKC/casefold alphanumerics (ignores punctuation, wraps and spacing); numbers compared to 2 decimals. Replaces `vendor`/`customer` with `vendor_label`/`customer_label` (name + address against the dataset's combined label) and drops the deprecated `line_item_amounts`. |
| Coverage | Share of scored fields where the provider returned a value. |
| Whole-document accuracy | Every scored field correct (exact or normalized). |
| Line items P/R/F1 | Greedy one-to-one matching on description + gross amount (strict, and normalized descriptions). An invalid response counts all its expected rows as missed. |
| Invalid response rate | Outputs that failed schema/JSON validation (counted as wrong everywhere). |
| Review flags | Flagged = status ≠ `valid_reconciled`. The report gives P(wrong given flagged), P(flagged given wrong), and the count of wrong documents that were **not** flagged; that last one is the dangerous case. |
| Latency | Per document and per page (median, p90, max), split into load, parse and extract. |
| Memory | Peak RSS of the process and of child processes (Tesseract and RapidOCR run as children). On Windows, install `psutil` for the process figure. |
| Tokens / cost | Summed vendor-reported usage; cost only from configured prices. |

Intervals are 95%: Wilson for proportions and a seeded percentile bootstrap over documents for micro accuracy and F1. With 50 rows a difference of a few points is usually inside the noise. Read the paired `compare` interval, not the two means.

## Leakage guards

- Retrieval (`--rag`, vision-api only) indexes the training split; `TrainingRetriever` refuses validation and test data and returns format shapes, never values.
- The test split needs `--final-test-run`, a frozen configuration from a validation report, and all rows (no `--limit`). The frozen fingerprint covers the extraction code, provider, model revision, prompt version, parser engine and version, and limits; any change refuses the run. Each configuration runs on the test split once (`reports/test-runs.jsonl`).
- Layout rules in `ocr_mapping.py` use generic labels and geometry only. They were written against the supplied fixture (`invoice-242`) and synthetic layouts, not against validation or test rows.

## Known caveats

- The dataset is synthetic and essentially one template family, and the Donut checkpoint was fine-tuned on it. Neither the OCR rules nor Donut results generalise to other layouts without separate labelled data.
- The OCR provider's evidence quotes come from its own OCR text, so its evidence checks show traceability, not independent confirmation. The IBAN checksum and arithmetic checks are the independent signals.
- Smoke runs on synthetic copies of the fixture exist only to check that the harness works; they are not accuracy results.
