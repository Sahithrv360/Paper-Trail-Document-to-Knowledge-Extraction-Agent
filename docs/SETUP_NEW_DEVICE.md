# Run the Paper Trail MVP on a New Device

These steps reproduce the local MVP setup without copying virtual environments or model caches. Install [Python 3.12](https://www.python.org/downloads/windows/) (the package requires Python 3.11 or newer), [Node.js LTS](https://nodejs.org/en/download), and [Git for Windows](https://git-scm.com/download/win). [Ollama](https://ollama.com/download) is needed for local model answers. [Docker Desktop](https://www.docker.com/products/docker-desktop/) is needed only if you want Neo4j locally. This guide uses Windows PowerShell; use the corresponding Tesseract, Docker, and Ollama installers for another operating system.

## 1. Get the repository

```powershell
git clone https://github.com/Sahithrv360/Paper-Trail-Document-to-Knowledge-Extraction-Agent.git
cd Paper-Trail-Document-to-Knowledge-Extraction-Agent
```

If your team is using a branch or commit other than `main`, check it out before installing packages. Do not copy `.venv`, `node_modules`, Python caches, or model cache folders from the old computer.

## 2. Set up Python and the core agent

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

The root `pyproject.toml` installs the `kg-agent` package and its required Python libraries. The local review server itself uses the Python standard library; optional capabilities have separate requirements below.

Copy the example configuration and edit only the values needed on this device:

```powershell
Copy-Item .env.example .env
```

Keep `.env` private. At minimum, set `WATCH_FOLDER` to the directory that will receive documents. SQLite paths can remain at their defaults.

The existing development machine currently uses RapidOCR and Ollama. For the same local configuration on a new device, install the RapidOCR option in step 4, then set `EXTRACTION_PROVIDER=ocr` and `PARSER_ENGINE=rapidocr` in `.env`. A fresh `.env.example` deliberately starts with extraction disabled (`EXTRACTION_PROVIDER=none`).

## 3. Set up local Ollama models

Install Ollama from [ollama.com/download](https://ollama.com/download), start its service, then pull the models used by the example configuration:

```powershell
ollama pull qwen2.5:3b-instruct
ollama pull nomic-embed-text
```

- `qwen2.5:3b-instruct` is the checked-in example default for graph-answer and agent response generation (`GRAPH_RAG_MODEL`). It is the local answer model; replace it with another installed chat model by changing `.env`.
- `nomic-embed-text` is used for dense retrieval embeddings. It is separate from the chat model.
- If `GRAPH_RAG_MODEL` is blank in Agent mode, the code fallback is `qwen3:4b`; it is not the model selected by the checked-in `.env.example`.
- An optional OpenRouter route is configured with `AGENT_LLM_PROVIDER=openrouter`, `OPENROUTER_MODEL`, and `OPENROUTER_API_KEY`. The example model ID is `qwen/qwen3.8-flash`. This sends the question and retrieved graph evidence to a hosted service. Do not use this route for data that must stay local.

Ollama model weights are downloaded to Ollama's own local model store and are not included in Git. The first pull requires internet access. Afterward, Ollama can run those models locally. Keep `OLLAMA_HOST=http://127.0.0.1:11434` for local use.

If you want the optional Sentence Transformers fallback instead of Ollama embeddings, install the extra with `python -m pip install -e ".[embeddings]"`. Its default fallback model, `BAAI/bge-small-en-v1.5`, downloads on first use. This extra is not needed when the Ollama `nomic-embed-text` model is available.

## 4. Optional extraction models and OCR programs

Choose only the extraction path you need; these are independent options.

### Donut invoice model

Install the PyTorch/Transformers requirements:

```powershell
python -m pip install -r backend/requirements-extraction.txt
```

The pinned Hugging Face checkpoint is `katanaml-org/invoices-donut-model-v1` at revision `1745f5a29c37bc5d2e16ab5e2bc6aba57fdd6f7c`. It downloads on first inference. Set `EXTRACTION_PROVIDER=donut` in `.env`. For NVIDIA GPU inference, install a PyTorch build compatible with the computer's CUDA driver and set `EXTRACTION_DEVICE=cuda`; otherwise leave `cpu`. The checkpoint, Python/PyTorch version, and CUDA compatibility affect whether GPU inference works.

### Tesseract OCR with mapped invoice fields

Install the Tesseract system program separately (for Windows, the UB Mannheim build can be installed with `winget install --id UB-Mannheim.TesseractOCR --exact --accept-source-agreements --accept-package-agreements`), then install Python image/PDF support:

```powershell
python -m pip install -r backend/requirements-ocr.txt -r backend/requirements-pdf.txt
```

Set `EXTRACTION_PROVIDER=ocr`, `PARSER_ENGINE=tesseract`, and, if Tesseract is not on `PATH`, set `TESSERACT_CMD` to its executable path. The Python package installation does not install the Tesseract executable.

Check the installation with `tesseract --version` in a new terminal. If Windows cannot find the command, add the Tesseract install directory to `PATH` or use its full executable path in `TESSERACT_CMD`.

### RapidOCR alternative

```powershell
python -m pip install -r backend/requirements-rapidocr.txt
```

Set `EXTRACTION_PROVIDER=ocr` and `PARSER_ENGINE=rapidocr`. This is an optional local CPU path and can use substantially more memory than Tesseract.

### PaddleOCR preview

```powershell
python -m pip install -r backend/requirements-paddleocr.txt
```

PaddleOCR is a standalone OCR/layout preview command, not the invoice-field mapper used by the watched-folder pipeline. It downloads its OCR models on first use.

### Hosted vision extraction

The optional provider uses a configured Anthropic Messages API model. Set its API key and model, and set `PAPERTRAIL_ALLOW_REMOTE` to the exact acceptance phrase documented in `.env.example`. This transmits document content to the provider. Leave it unset to keep extraction local.

## 5. Optional Neo4j

SQLite works without Docker and remains the source of truth. To use the Neo4j projection, install and start Docker Desktop, then launch a local Neo4j container. Replace the sample password in the command with a private development password and use the same value in `.env`:

```powershell
docker run --name papertrail-neo4j --restart unless-stopped -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/replace-this-password -v papertrail-neo4j-data:/data -d neo4j:5
```

The Neo4j Browser is available at `http://localhost:7474`; the application connects over Bolt on port `7687`. Then install the Python driver:

```powershell
python -m pip install -r backend/requirements-neo4j.txt
```

Set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and `NEO4J_DATABASE` in `.env`, then sync the local SQLite graph:

```powershell
python -m backend.app.neo4j_sync
```

Neo4j credentials and database contents are device-specific; the SQLite ledger is not automatically copied by `git clone`.

## 6. Install and run the frontend

In a second PowerShell terminal, from the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

The development UI is served at `http://localhost:5173` and proxies API requests to the review server at `http://127.0.0.1:8765` by default. Use `PAPERTRAIL_API_TARGET` if you changed the backend address.

In the first terminal, from the repository root with the Python virtual environment active:

```powershell
python -m backend.app.review.server
```

The backend prints its token link on port `8765`. Copy its `#token=...` fragment into `http://localhost:5173/#token=...` to open the React UI. The server binds to loopback by default. Keep the token private.

## 7. Bring over local data deliberately

The Git checkout does not include your local `.env`, private invoice files, SQLite databases, `watched/` contents, generated embeddings, run logs, or downloaded model weights. If you need those on the new device, transfer only the necessary files through an approved private method. Do not commit credentials or confidential customer documents. Set the new `.env` paths and verify source database backups before using them.

## Common checks

```powershell
python -m unittest discover -s backend/tests -v
python -m backend.app.review.server
```

The test suite does not download the Donut model or establish extraction accuracy on your invoices. For reproducible evaluation steps and limitations, see [`EVALUATION.md`](EVALUATION.md) and [`MILESTONES_REPORT.md`](MILESTONES_REPORT.md).

To install the test helper tools as well as the application, use `python -m pip install -e ".[dev]"` in place of `python -m pip install -e .` above.
