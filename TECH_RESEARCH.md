# OCR and Extraction Technology Research

Research snapshot: 24 October 2026. Confirm model IDs, versions, pricing, supported platforms, and provider data-retention terms from vendor documentation immediately before implementation; these details change.

## What fits this laptop and architecture

The user's PP-StructureV3 run initialized separate document-layout, text detection/recognition, orientation, table classification, table-structure, and table-cell models. It is a full document-layout pipeline, and in this case it is too resource-intensive or unstable on the user's Windows CPU. Disabling MKL-DNN addresses a specific backend path only; it does not make the whole pipeline lightweight. Do not use PP-StructureV3 in the live watcher by default.

Prefer a selectable extraction pipeline:

1. **Digital PDF:** extract its embedded text and page coordinates locally first. Avoid rasterizing pages when the PDF already contains text.
2. **Scanned PDF/image, local mode:** use a lightweight OCR adapter based on ONNX Runtime (RapidOCR/PP-OCR tiny or small models) or a system Tesseract install. Process one page at a time with bounded resolution and one worker. OCR returns text/boxes, not reliable invoice semantics or graph relations.
3. **Structured invoice extraction, API mode:** send only the necessary page/image (or OCR text plus targeted crops) to a configurable vision-language API, request schema-constrained JSON, and pass the result through the same local validation/evidence pipeline. A vision model can read tables and party blocks together; it still can omit rows or misread digits and must be scored.
4. **Knowledge graph:** create/update graph nodes and relationships deterministically from validated, evidence-backed invoice records. The model may propose fields and relation candidates in a constrained schema; never let it emit arbitrary Cypher/SQL or directly mutate graph storage.

The project has an existing provider seam and SQLite graph store, so no model needs to be trained from scratch. Use the pretrained model through an API or the small local OCR weights, and preserve Donut as a measured comparison baseline until its replacement has demonstrated improvement.

## Options considered

| Option | Strengths | Costs and limits | Recommendation |
|---|---|---|---|
| PP-StructureV3 (current preview) | OCR plus layout and table structure | Multiple models, slower start/inference, current Windows CPU failure/slowdown; output is still not invoice fields | Keep out of the default runtime; retain only as optional experiment until verified |
| RapidOCR / PP-OCR via ONNX Runtime | Local/offline, small model variants and CPU-compatible runtime options; useful for text/boxes | OCR only; custom table/party/line-item mapping is still needed; OCR errors remain | Preferred local fallback; benchmark tiny/small model settings on validation data |
| Tesseract | Small local dependency, mature, no large VLM checkpoint | Table layout and reading order are limited; mapping is custom | Useful lightweight baseline or fallback |
| Docling with RapidOCR | PDF text extraction, layout and table document representation; pluggable OCR engines | Broader pipeline and dependencies may still be heavier than a direct OCR call; test on Windows and memory budget | Consider for mixed digital/scanned PDF needs, not mandatory for JPG-only first phase |
| Hosted vision API (e.g. Gemini, OpenAI, Anthropic) | No model weights or sustained local inference; direct image/PDF understanding and structured outputs are available in current vendor APIs | Per-request cost, network dependency, rate limits, vendor retention/data policy, and confidential documents leave laptop | Best candidate for strong extraction on constrained hardware, only behind explicit opt-in and provider abstraction |
| Local vision-language model (e.g. PaddleOCR-VL family / Qwen-VL) | Offline after setup; direct document understanding | Requires model memory, supported inference runtime and often GPU/CUDA; 0.9B parameters is not equivalent to a lightweight CPU pipeline | Do not make default on this laptop; evaluate only after hardware and runtime compatibility are established |

## Primary documentation

- PaddleOCR general OCR and PP-StructureV3: <https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html> and <https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PP-StructureV3.html>.
- PaddleOCR-VL 1.6 overview and deployment: <https://www.paddleocr.ai/main/en/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.6.html> and <https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html>. The vendor describes a 0.9B VLM and documents accelerated deployment routes; that does not establish suitability for this Windows CPU laptop.
- Docling OCR engine support and installation: <https://docling-project.github.io/docling/concepts/OCR/> and <https://docling-project.github.io/docling/getting_started/installation/>. Its catalog includes Tesseract, EasyOCR, RapidOCR (including ONNX Runtime/OpenVINO/Paddle backends), and table-structure processing.
- Gemini document understanding: <https://ai.google.dev/gemini-api/docs/document-processing> and structured outputs: <https://ai.google.dev/gemini-api/docs/structured-output>. Current docs describe native PDF/image understanding and schema-constrained JSON. Confirm model availability, current pricing, and data handling before choosing it.
- OpenAI vision input and structured output docs: <https://platform.openai.com/docs/guides/images> and <https://platform.openai.com/docs/guides/structured-outputs>.
- Anthropic vision and PDF docs: <https://docs.anthropic.com/en/docs/build-with-claude/vision> and <https://docs.anthropic.com/en/docs/build-with-claude/pdf-support>.
- Local pretrained Donut baseline: <https://huggingface.co/katanaml-org/invoices-donut-model-v1>; dataset: <https://huggingface.co/datasets/katanaml-org/invoices-donut-data-v1>.

## Recommended decision for the first implementation slice

Implement a lightweight local OCR adapter and one opt-in hosted vision provider behind the existing extraction-provider interface. Do not install every candidate or call a service at import/startup. Let environment configuration select `none`, `donut`, `rapidocr`, or a single chosen API provider; default to no extraction and keep remote transfer explicitly disabled until the user supplies a key and enables it. Store provider/model/version, selected image/page parameters, latency, token usage/cost if available, and retrieval IDs in provenance. Avoid fixed pricing claims in README; report actual API usage returned by the provider.

For laptops, enforce page count, pixel count, timeout, concurrency, and request-size limits. Provide a CPU-only configuration that runs one document at a time and a no-model queue-only mode. A provider exception must be visible in job state; do not silently fall back to an external provider.

## Taxonomy and graph

Use an invoice ontology (typed entities and evidence-backed relations) before a product taxonomy. Example graph edges: `Document -SUPPORTS-> Invoice`, `Vendor -ISSUED-> Invoice`, `Invoice -BILLED_TO-> Customer`, `Invoice -CONTAINS-> LineItem`, and optionally `LineItem -CLASSIFIED_AS-> ProductCategory`. Product categories can be added only when extraction/classification has evidence and the category is useful. LangChain Graph RAG is a retrieval integration, not a substitute for the persistent graph store or deterministic graph writer: <https://docs.langchain.com/oss/python/integrations/retrievers/graph_rag>.

The current Hugging Face dataset is synthetic and European-label-oriented, while the supplied regression fixture uses a dollar symbol. The target jurisdiction for future real invoices is unknown. Keep core fields jurisdiction-neutral and support country-specific extensions (such as GSTIN/CGST/SGST/IGST, HSN/SAC, IFSC, IBAN, and locale-specific numeric parsing) without assuming any one set applies to all documents.
