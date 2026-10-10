# Paper Trail — Document-to-Knowledge Extraction Agent

Paper Trail is a capstone project focused on extracting structured, evidence-backed knowledge from enterprise documents such as invoices and contracts.

Instead of treating OCR or PDF text extraction as the final result, the system aims to identify entities, relationships, and business facts, connect them to existing master records only when supported by evidence, and flag ambiguous cases for human review.

> **Project status:** Initial setup and documentation. The end-to-end extraction pipeline, evaluation results, and deployment are not yet verified.

## Problem Statement

Enterprise documents contain important business information in unstructured formats. Extracting text alone does not determine whether a claim is supported, which master record an entity represents, or whether a contract clause describes a completed event or a conditional possibility.

Paper Trail aims to address these challenges through evidence provenance, structured validation, conservative entity resolution, and explicit uncertainty handling.

## Objectives

- Process digital and scanned PDF documents.
- Extract entities, relationships, and business facts.
- Attach source evidence and page references where available.
- Compare extracted entities with existing master records.
- Leave ambiguous links unresolved and route them for review.
- Preserve conditional and uncertain language.
- Evaluate accuracy, reliability, latency, and cost.

## Proposed Architecture

The planned processing pipeline is:

1. Document upload and validation.
2. PDF text extraction and OCR fallback.
3. Text preprocessing and source-location preservation.
4. Entity, relationship, and fact extraction.
5. Structured-output validation.
6. Evidence-support validation.
7. Master-record candidate generation and entity resolution.
8. Human review for uncertain cases.
9. Structured result storage and presentation.
10. Evaluation and observability.

See [`architechture.png`](./architechture.png) for the initial architecture image. The diagram should be reviewed and updated to reflect the implemented system.

## Planned Technology Stack

The technology stack remains subject to capstone constraints and testing.

| Component | Proposed option |
|---|---|
| Application language | Python |
| API | FastAPI |
| PDF processing | A PDF text-extraction library |
| OCR | An OCR engine suitable for the selected environment |
| Structured validation | Pydantic or JSON Schema |
| Initial metadata storage | SQLite |
| Knowledge extraction | Replaceable LLM interface |
| Evaluation | Versioned labelled examples and a repeatable evaluation runner |
| User interface | A lightweight document-review interface |

These are proposed choices, not a statement that the components are already implemented.

## Repository Structure

The planned structure is:

```text
Paper-Trail-Document-to-Knowledge-Extraction-Agent/
├── README.md
├── APPROACH.md
├── ARTEFACT.md
├── architechture.png
├── docs/
│   └── functional.md
├── src/
│   └── paper_trail/
├── prompts/
├── data/
│   ├── synthetic/
│   └── evaluation/
├── tests/
├── evals/
├── scripts/
├── .env.example
├── .gitignore
└── requirements.txt
```

This is the intended structure; directories and files should be added as they become necessary. Keep held-out evaluation data separate from development data.

## Getting Started

### Prerequisites

- Python version compatible with the selected dependencies.
- Git.
- An OCR engine if required by the chosen OCR integration.
- Model-provider credentials only if an approved hosted model is selected.

### Setup

Clone the repository:

```bash
git clone https://github.com/Sahithrv360/Paper-Trail-Document-to-Knowledge-Extraction-Agent.git
cd Paper-Trail-Document-to-Knowledge-Extraction-Agent
```

Create and activate a virtual environment.

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies after `requirements.txt` has been created:

```bash
pip install -r requirements.txt
```

Application run commands and test commands will be documented after the corresponding entry points are implemented.

## Example Expected Behaviour

### Ambiguous Entity Linking

If an invoice names "Sri Venkateswara Traders Pvt Ltd" and the vendor master contains similar names, the system should show candidate matches and their supporting evidence. It should leave the link unresolved if the available evidence is insufficient.

### Conditional Contract Clause

If a contract states that it "may be renewed for a further term by mutual consent of the parties", the system should extract a conditional renewal clause. It should not assert that renewal has occurred or is guaranteed.

These are required target behaviours. Their correctness must be verified through tests.

## Evaluation

The planned evaluation covers:

- Entity and relationship precision, recall, and F1.
- Evidence-support accuracy.
- Entity-linking precision and candidate ranking.
- Conditional-obligation interpretation.
- Schema validity and correct abstention.
- Latency, cost, failure rate, and reviewer corrections.

Evaluation results will be added only after repeatable runs against labelled data.

## Privacy and Security

- Use synthetic documents during initial development.
- Do not commit API keys or secrets.
- Do not send sensitive documents to external services without appropriate approval.
- Avoid logging raw document contents unless specifically justified and approved.
- Document dataset provenance and licences.
- Keep confidential documents and local evaluation artifacts out of the public repository.

## Documentation

- [`docs/functional.md`](./docs/functional.md) — requirements and acceptance criteria.
- [`APPROACH.md`](./APPROACH.md) — system design, data, evaluation, and risks.
- [`ARTEFACT.md`](./ARTEFACT.md) — implementation history, evaluation results, limitations, and deployment status.

## Contributions

The team should record actual ownership, meaningful prototype changes, and reproducible evaluation results. Prototype tags should correspond to working, tested versions.

## Licence

The project licence has not yet been selected. Confirm the team's intended licence and any third-party dependency requirements before adding a licence file.