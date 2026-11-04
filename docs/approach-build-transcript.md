# AI Transcript — MVP Approach and Build Artifacts

**Project:** Paper Trail — Document-to-Knowledge Extraction Agent  
**Purpose:** Curated record of AI-assisted preparation and later correction of the approach/build artifacts. Earlier notes are retained as history; the current implementation status is in [`APPROACH.md`](../APPROACH.md) and [`build-plan.md`](build-plan.md).

## Historical Gate 1 excerpt — 24 October 2026

**User:** Gate 1 covers how we will solve the problem and build it. The expected items are an approach document at `APPROACH.md`, an architecture diagram, a UX prototype link, and a build plan. The gate also asks for one AI transcript.

**Assistant:** Reviewed the repository requirements and existing implementation notes. Prepared an approach document, a phased build plan, and a static reviewer-flow prototype.

**User:** Create a `CODEX.md` file such that another agent knows what all it needs to do.

**Assistant:** Added a project handoff with the current state, prioritized work, guardrails, useful commands, and documentation references.

**User:** Check the `pics` folder and understand what all I need to fill.

**Assistant:** Reviewed the Gate 1 screenshot. It showed fields for the approach, optional architecture and UX links, an optional build-plan path, and an AI transcript link or file. The form's build-plan field referred to `docs/build-plan.md`.

## Current-state correction — 4 November 2026

The original Gate 1 notes described an earlier repository snapshot and are not a reliable description of the current MVP. The project has since added the extraction and review pipeline, SQLite ledger/graph, optional Neo4j projection, React frontend, graph QA, and local/hosted model configuration. The artifacts were updated to reflect those changes and to make clear that implementation is not the same as production readiness:

- [`APPROACH.md`](../APPROACH.md) now distinguishes implemented behavior, operating rules, and remaining validation work.
- [`build-plan.md`](build-plan.md) now lists implemented workstreams, open checks, and the configured model inventory.
- [`SETUP_NEW_DEVICE.md`](SETUP_NEW_DEVICE.md) documents a clean Windows setup, local model installation, optional OCR/Neo4j packages, and how to run the backend and frontend.
- [`README.md`](../README.md) now points to the same current setup and implementation information.

## Current model and setup notes

- The checked-in `.env.example` selects Ollama and `qwen2.5:3b-instruct` for local answer generation; dense retrieval uses `nomic-embed-text`.
- The invoice Donut adapter pins `katanaml-org/invoices-donut-model-v1` to a specific Hugging Face revision. Tesseract, RapidOCR, and PaddleOCR are separate OCR options with distinct installation paths; PaddleOCR is currently a preview tool.
- OpenRouter is optional and configured with the example model ID `qwen/qwen3.8-flash`. Hosted calls send the question and retrieved graph evidence to the provider.
- Models and credentials are not committed. A new device must install software and download any local weights it needs; clone-only setup does not copy ignored data, databases, or model caches.

## Accuracy and evidence limits

The model names and provider defaults above come from the current repository configuration. They do not establish comparative model quality. The current synthetic fixtures and manual query examples are not a labeled production evaluation. Invoice accuracy and answer quality must be measured against labeled data before reporting performance claims. Confidence is a deterministic triage score, and synthetic communications are not real business evidence.
