# Paper Trail — Approach & Build

This document describes the intended solution and how the team will build it. The repository is an evolving prototype; current implementation and measured results are called out separately from planned capabilities. The engineering record is in [`docs/SDD.md`](docs/SDD.md), and the phased delivery sequence is in [`docs/build-plan.md`](docs/build-plan.md).

## Who this is for

The first users are procurement or finance operations staff who receive invoices and contracts and need to check key facts against source documents. Reviewers need to see where each extracted value came from, understand uncertainty, and decide when a vendor match needs human attention. Developers and evaluators need repeatable runs and traceable outputs. The prototype is not intended to make legal decisions, trigger payments, or update authoritative enterprise records.

## Problem and outcome

OCR and PDF parsing make text readable but do not establish which entities it refers to, how they relate, or whether a statement is conditional. Paper Trail aims to turn procurement documents into structured, evidence-backed knowledge while preserving ambiguity and source provenance.

The target scope includes invoices and contracts, digital and scanned PDFs, extraction, evidence validation, conservative master-record linking, human review, and repeatable evaluation. The first implemented vertical slice is narrower: local watched-folder processing for JPG/PNG invoices, a pretrained Donut model, validation, and SQLite persistence. PDF extraction, contract understanding, a review interface, and live master-data integration are planned, not implemented.

## Build versus reuse

- **Reuse:** the existing folder watcher and job registry, canonical invoice schema, Donut checkpoint adapter, dataset/evaluation utilities, SQLite graph store, synthetic invoice fixture, and public invoice dataset for an offline baseline. Dataset provenance is documented in `data/README.md`.
- **Build:** the missing reviewer experience, evidence-aware extraction path, PDF/OCR integration, conservative candidate linking, and labelled evaluation cases for the target behaviours.
- **Defer:** replacing the pretrained model, hosted model calls, enterprise master-data connectors, and production deployment. These depend on privacy constraints and measured validation performance. Future linking should consume an approved source of master records; this project does not recreate an enterprise warehouse.

## Architecture

The proposed flow is document intake → text extraction/OCR → fact and relationship extraction → schema and evidence validation → candidate generation and conservative entity resolution → human review when uncertain → structured storage and evaluation. Deterministic code handles file validation, schema enforcement, persistence, access controls, and metric calculation. A replaceable model component may interpret document language, but its confidence statement alone is not proof of a fact or identity.

The current architecture image is [`assests/paper-trail-architecture.png`](assests/paper-trail-architecture.png). It is a starting point; implementation status and gaps are recorded in [`docs/SDD.md`](docs/SDD.md).

### Human review and failure paths

- Ambiguous entity matches remain unresolved until reviewed.
- Unsupported or contradictory claims are flagged.
- Invalid output is retried only under a controlled policy; persistent failures are visible.
- Unreadable documents and OCR failures are surfaced.
- No uncertain link is silently turned into an authoritative relationship.
- Review decisions should be recorded with provenance when that capability is implemented.

## Data and evaluation

Use synthetic documents and synthetic master records for product development. Keep development and held-out evaluation data separate and document data provenance and licences. The initial semantic model includes Vendor, Invoice, Contract, PaymentTerm, and RenewalClause, with source mentions kept distinct from resolved master entities.

Required challenge cases include an ambiguous vendor name and a contract clause stating renewal may occur only by mutual consent. The system must not force the vendor link or represent conditional renewal as completed or guaranteed.

Evaluation should measure entity and relationship precision/recall/F1, evidence support, linking and candidate ranking, conditional-language interpretation, abstention, schema validity, failures, latency, cost where measurable, and reviewer corrections. Report sample counts, configuration, and limitations. The recorded five-row Donut test baseline is 28/75 exact fields (37.3%) and 0/5 exact documents; it is a small baseline, not evidence of general accuracy or improvement.

## UX prototype and build plan

The static reviewer-flow sketch is [`docs/prototype.html`](docs/prototype.html). It shows a document queue, extracted facts, source evidence, and an unresolved vendor match. It uses sample content and is not connected to project data or an API.

The build phases and exit evidence are in [`docs/build-plan.md`](docs/build-plan.md). The next technical increment is page-aware PDF/OCR ingestion with evidence spans and labelled validation, followed by a minimal review surface.

## Risks and guardrails

Hallucinated facts, incorrect entity links, OCR errors, conditional-language mistakes, privacy exposure, evaluation leakage, and uncontrolled model costs are the main risks. Require source evidence, validate outputs in code, abstain on weak matches, use synthetic data initially, keep secrets out of version control, and do not send sensitive documents to hosted services without approval. Do not claim a quality gain until it is measured on labelled validation data.

Open decisions include provider approval, deployment restrictions, expected document volume, retention rules, evaluation thresholds, and the review process. Actual implementation and results should remain synchronized with `docs/SDD.md` and `artefact.md`.
