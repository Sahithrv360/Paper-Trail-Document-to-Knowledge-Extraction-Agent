# Paper Trail Project Report

**Project:** Paper Trail — Document-to-Knowledge Extraction Agent
**Report date:** 4 November 2026
**Status:** Working local MVP; a local upload-to-extraction smoke check passed with review flags. Container deployment configuration is prepared but not deployed. A representative labeled accuracy evaluation has not yet been completed.

## 1. Executive summary

Paper Trail ingests invoice images and PDFs, extracts structured invoice data with supporting evidence, tracks extraction and review history, stores supported facts in SQLite, and exposes them through a React dashboard and graph-grounded question answering. Its design favors a local-first workflow: SQLite is authoritative, extraction providers are explicitly selected, and remote document processing requires a separate opt-in.

The repository now includes a Docker-based deployment path intended for a persistent container host such as Render. Vercel can host static assets, but it is not the recommended single-host deployment for this application: the backend runs a long-lived watcher and writes uploaded files and SQLite databases, which require persistent storage. Render documents persistent disks for paid services and ephemeral filesystems by default ([persistent disks](https://render.com/docs/disks), [web services](https://render.com/docs/web-services)). Vercel Functions have request-duration limits and are request-oriented ([Vercel Function limits](https://vercel.com/docs/functions/limitations)).

## 2. Goals and scope

The MVP is intended to demonstrate the complete document-to-knowledge workflow:

- Accept supported invoice PDFs and images.
- Track file versions and processing jobs.
- Extract invoice header fields and line items.
- Preserve evidence, provenance, validation results, confidence triage, and review history.
- Present documents, facts, entities, relationships, and graph-backed answers in a browser UI.
- Keep review and correction steps explicit and auditable.

It is not a production accounting or payment approval system. Confidence is a rule-based quality proxy, not a calibrated probability. A shared item description is a text match and does not establish product identity. The question-answering agent is read-only and does not create graph facts.

## 3. Architecture and stack

| Layer | Technology | Role |
|---|---|---|
| Browser UI | React 19, Vite, React Router, Recharts, Sigma/Graphology, XYFlow | Dashboard, document views, review, graph exploration, analytics, and knowledge questions |
| HTTP service | Python standard-library `ThreadingHTTPServer` | Serves the built UI and authenticated JSON/multipart API |
| Ingestion | Python watcher, registry, SQLite job state | Scans the watched directory, detects versions, and queues/recovers work |
| Extraction | Donut or OCR providers; optional hosted vision provider | Produces structured invoice data; provider is selected through environment settings |
| OCR and document input | RapidOCR, optional Tesseract; pypdfium2 and Pillow | Reads scanned images and PDFs and renders pages within configured bounds |
| Validation and evidence | Python schema, validation, matching, evidence, and confidence modules | Normalizes fields, checks totals and relationships, and retains evidence/provenance |
| Authoritative persistence | SQLite ledger and graph stores | Stores documents, extraction versions, reviews, facts, and graph relations |
| Optional graph projection | Neo4j | Rebuildable graph projection; SQLite remains authoritative |
| Question answering | Graph retrieval and optional local Ollama or explicitly enabled hosted model | Finds graph evidence and formats cited answers |
| Deployment | Docker; Render blueprint with persistent disk | Builds the React app and Python service into one deployable container |

## 4. End-to-end workflows

### 4.1 Upload and ingest

1. A user opens the app using a URL containing the review token in the URL fragment. The frontend saves it in session storage and sends it on API calls as `X-Review-Token`.
2. The upload dialog obtains supported file extensions and the size limit from the backend.
3. The browser posts each selected PDF/JPEG/PNG using the authenticated multipart upload route.
4. The backend validates the filename, extension, and size, then writes the file atomically into `WATCH_FOLDER`.
5. The watcher registers new or changed files, hashes content, versions documents, and records queued work. Interrupted jobs are recoverable on restart.

### 4.2 Extract and validate

1. The selected provider loads the document within page, pixel, and file-size bounds.
2. A model or OCR engine extracts text and structured fields, including invoice identifiers, parties, dates, amounts, currency, and line items where available.
3. The pipeline maps and normalizes output, attaches evidence/provenance, checks schema and arithmetic/reconciliation conditions, and records provider/model metadata.
4. A deterministic confidence proxy and validation status help triage records. Low-confidence or ambiguous records remain reviewable; the score does not authorize payment.

Available choices include local Donut inference, OCR field mapping with Tesseract or RapidOCR, and an opt-in remote vision API. Remote invoice extraction requires an explicit acceptance phrase, credentials, and model configuration. Keep this boundary in mind when choosing a hosted provider.

### 4.3 Persist, review, and correct

1. SQLite stores the document registry, extraction versions, supported facts, relationships, and review events.
2. The UI shows document state, extracted values, evidence, and changes between versions.
3. Review actions are bound to the extraction version displayed to the user, preventing a stale screen from approving a newer extraction.
4. Corrections are recorded as review history and update the effective record. Neo4j, if configured, is a derived projection and can be rebuilt from SQLite.

### 4.4 Explore and ask

The UI reads document, analytics, evidence, graph, and review endpoints. Graph retrieval returns source-linked context for standard answers. Agent mode can track query stages and use a configured language model to phrase the answer. List questions such as “who bought this item?” use a deterministic `LineItem → Invoice ← Customer` traversal, with a citation per matched customer/invoice so longer buyer lists are not lost to ordinary top-k reranking. The agent remains read-only; citations and supporting graph records are returned to the interface.

## 5. Security, privacy, and data handling

- API routes require a review token compared in constant time. The browser token is kept in the URL fragment, which is not included in HTTP requests.
- The local default binds to loopback and rejects non-loopback hosts and cross-origin requests.
- Public container binding requires a configured host allow-list (`PAPERTRAIL_PUBLIC_HOSTS`). Configure the exact deployed hostname(s), use HTTPS at the host, and set a long random `REVIEW_TOKEN` in the host's secret store.
- Uploaded files, SQLite databases, and graph data must live on persistent storage. Do not store them in the container image or commit real invoices or secrets.
- A hosted extraction provider receives document content. Use it only when document transfer is permitted and the explicit remote opt-in is configured.
- The Render blueprint persists `/var/data`; set `WATCH_FOLDER`, `DATABASE_PATH`, and `GRAPH_PATH` to that mounted location.
- A public deployment needs additional operational controls before handling sensitive production invoices, including access management, backups/restore drills, monitoring, retention rules, and a formal security review.

## 6. Development and deployment

### Local development

Use Python 3.11+ (3.12 recommended) and Node.js. Install the Python package and the requirements for the chosen extraction route. Run the review server from the repository root and the Vite frontend from `frontend/`. The local Vite server proxies `/api` to port 8765.

### Container deployment

`Dockerfile` builds the frontend assets and Python service together. `render.yaml` describes a Render web service, persistent storage, OCR defaults, and the watcher. To deploy:

1. Push the repository to a Git provider connected to Render.
2. Create a Render service from the repository using the included blueprint.
3. Set `REVIEW_TOKEN` to a generated high-entropy secret. The server accepts Render's assigned `RENDER_EXTERNAL_HOSTNAME` automatically; add `PAPERTRAIL_PUBLIC_HOSTS` only for additional custom hostnames.
4. Keep the persistent disk mounted at `/var/data`; confirm all three data paths point there.
5. Deploy, open the service URL, and append `/#token=<your-review-token>` to the browser URL.
6. Upload the synthetic smoke invoice and check whether it reaches extracted/reviewable state.

The included blueprint expects a paid Render web service because Render does not attach persistent disks to free web services ([Render free service limitations](https://render.com/docs/free)). It has not been deployed from this workspace, so platform build/runtime behavior still needs confirmation. The Python server prints a local token URL when it starts; when `REVIEW_TOKEN` is configured, use that configured secret with the public HTTPS URL.

Vercel can serve a separately built static frontend, but this project would still need a separately hosted, persistent backend. Because the frontend currently calls same-origin `/api` and the backend enforces same-origin and Host checks, a Vercel split deployment also needs a deliberate proxy/API-origin configuration. The included Render setup is the shorter path for this repository.

### Smoke invoice

`scripts/make_upload_smoke_invoice.py` creates `presentation_data/synthetic_invoices/upload-smoke-invoice.png`. It is explicitly labeled synthetic and contains an invented vendor, customer, invoice number, two line items, and totals. Generate it with:

```powershell
python scripts/make_upload_smoke_invoice.py
```

Upload it through the UI. The expected source values are invoice `PT-SMOKE-2026-001`, subtotal EUR 37.00, tax EUR 3.70, and total EUR 40.70. OCR may vary; inspect the extracted evidence and review status rather than treating a successful upload as proof of accuracy.

## 7. Configuration

Important settings include:

- `WATCH_FOLDER`, `DATABASE_PATH`, `GRAPH_PATH`: watched files and SQLite storage.
- `POLL_INTERVAL_SECONDS`, `MAX_FILE_MB`, `MAX_PAGES`, `MAX_PAGE_PIXELS`: ingestion/resource limits.
- `EXTRACTION_PROVIDER`, `PARSER_ENGINE`, `EXTRACTION_DEVICE`: processing route.
- `PAPERTRAIL_ALLOW_REMOTE`, `VISION_API_KEY`, `VISION_API_MODEL`: explicit hosted extraction setup.
- `GRAPH_RAG_BACKEND`, `OLLAMA_HOST`, `AGENT_LLM_PROVIDER`: graph answer generation.
- `REVIEW_HOST`, `REVIEW_PORT`, `REVIEW_TOKEN`: web service binding and authentication.
- `PAPERTRAIL_PUBLIC_HOSTS`: optional additional hostnames accepted in public deployment mode; Render's assigned hostname is accepted automatically.
- `PAPERTRAIL_ENABLE_WATCHER`: enable the in-process watcher for the all-in-one deployment.

Use `.env.example` as the configuration reference. Keep `.env` and provider keys out of source control.

## 8. Validation and current limitations

The repository contains standard-library `unittest` coverage for extraction, ingestion, review API behavior, contracts, graph persistence, and related workflows. The project guide documents the suite command as `python -m unittest discover -s backend/tests -v`. A focused regression test now checks the buyer traversal and citations. On 4 November 2026, the synthetic invoice was uploaded through the authenticated HTTP endpoint to an isolated local server and processed by the integrated watcher with RapidOCR. Upload and extraction completed; invoice number, date, currency, total, and two item descriptions/amounts were read. Vendor and customer names, subtotal, tax, quantities, and item net amounts were not all extracted, so validation correctly marked the record `arithmetic_mismatch` and `needs_review` (confidence 0.62). This smoke run confirms the upload-to-review path, not extraction accuracy. No full suite or real model evaluation result is claimed in this report.

Current gaps include representative labeled evaluation, repeatable production backups, durable multi-user authentication/roles, deployment-specific monitoring, and a clean end-to-end deployment validation. A single container with SQLite is suitable for a demonstration or small single-user pilot; horizontal scaling and concurrent multi-instance writes need a different shared database/storage design.

## 9. Project materials

- `README.md`: setup and repository overview.
- `APPROACH.md`: current architectural principles and implementation choices.
- `docs/SDD.md`: system design.
- `docs/DATA_CONTRACTS.md`: data and evidence contracts.
- `docs/DATA_HANDLING.md`: local and hosted processing boundaries.
- `docs/EVALUATION.md`: evaluation approach and metrics.
- `docs/build-plan.md` and `docs/MILESTONES_REPORT.md`: implementation status and gaps.
- `render.yaml` and `Dockerfile`: container deployment configuration.
