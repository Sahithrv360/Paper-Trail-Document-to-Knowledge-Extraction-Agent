# Data Handling and Third-Party Components

**Date:** 2026-10-24. This describes what each extraction mode reads, stores and sends. Re-check vendor terms before enabling remote extraction; they change.

## What stays on this device

| Mode | Leaves the device? | Notes |
|---|---|---|
| `EXTRACTION_PROVIDER=none` (default) | No | Files are only hashed and queued. |
| `donut` | No | Downloads the model once from Hugging Face; documents are processed locally. |
| `ocr` (Tesseract or RapidOCR + layout rules) | No | OCR runs locally. Tesseract runs as a subprocess with one CPU thread; RapidOCR runs in a separate process. |
| PDF text layer (pypdfium2) | No | Embedded text is read locally; scanned pages are rendered locally. |
| Review server | No | Binds to 127.0.0.1 only and needs a per-start token (or `REVIEW_TOKEN`). |
| `vision-api` | **Yes** | Page images (JPEG, at most `VISION_API_MAX_IMAGE_PIXELS` each) are sent to the configured vendor. If a parser is selected, its OCR text is sent too, unless `VISION_API_SEND_PARSER_TEXT=0`. |

## Remote extraction opt-in

Remote extraction is off unless **all** of these are true. The watcher, the CLI and the benchmark refuse to start the provider otherwise, and list what is missing.

1. `PAPERTRAIL_ALLOW_REMOTE=I_ACCEPT_DOCUMENTS_LEAVE_THIS_DEVICE` (an explicit acceptance, not a boolean).
2. `VISION_API_KEY` (or `ANTHROPIC_API_KEY`) is set.
3. `VISION_API_MODEL` is set to a model ID taken from the vendor's current documentation. No model ID is hard-coded.

Use `python -m backend.app.extract_one FILE --provider vision-api --dry-run` to see the page count, the request size and any unmet conditions without sending anything.

The provider never routes a document to a different vendor on failure. It retries only transient errors (408/409/425/429/5xx/529 and network errors), with bounded backoff, and honours `retry-after`. Requests above `VISION_API_MAX_REQUEST_MB`, documents above `MAX_PAGES`, and non-transient rejections fail without sending or retrying.

**Logging.** The API key is sent only in the request header. It is never written to the record, provenance, logs or reports, and a test checks this. Vendor error messages are not echoed; only the error type is.

**Stored per extraction:** provider name, configured model, served model, request ID, stop reason, token usage, request size, page count, and a cost estimate only if you set `VISION_API_PRICE_*`. Raw request payloads (images) are not stored.

**Vendor choice.** The implemented backend is the Anthropic Messages API. It was chosen because:
- It documents JSON-schema structured outputs (`output_config.format`, no beta header) alongside image input.
- It uses plain HTTPS, so no SDK is needed.

This project was built with Claude, an Anthropic model, so weigh that when reviewing the choice. The provider class is vendor-neutral: another backend can be added beside it, and Gemini or OpenAI would be reasonable alternatives on price. Before enabling any vendor, confirm its current retention, training-use and regional terms for your account tier. For example, Google's Gemini billing page presents "prompts are not used to improve Google products" as a paid-tier property, so check which tier your key is on.

## Local storage

Every extraction version, and every review decision with the reviewer's name and note, is kept in the graph SQLite database. This is intentional, so audit history survives re-extraction. Removing a document deactivates its support but keeps its history.

To purge history for confidential documents, delete the database files; a selective purge command is not implemented. Databases, `reports/`, `watched/` and `.env` are git-ignored.

## Third-party components (licences checked 2026-10-24 from package metadata)

| Component | Use | Licence |
|---|---|---|
| Tesseract OCR 5.x (system binary) | Default local OCR | Apache-2.0 |
| rapidocr_onnxruntime 1.2–1.4 | Optional OCR engine (bundles PP-OCR ONNX models) | Apache-2.0 |
| onnxruntime | RapidOCR inference | MIT |
| opencv-python | RapidOCR dependency | Apache-2.0 |
| pypdfium2 5.x / PDFium | PDF text and rendering | Apache-2.0 or BSD-3-Clause (PDFium BSD-3-Clause plus bundled licences) |
| Pillow | Image I/O | MIT-CMU (HPND) |
| katanaml-org/invoices-donut-model-v1 | Donut baseline | See the model card |
| katanaml-org/invoices-donut-data-v1 | Evaluation data | MIT (dataset card); source invoices CC BY 4.0 (see `data/README.md`) |

Check each licence again before redistributing a bundle that includes these components.
