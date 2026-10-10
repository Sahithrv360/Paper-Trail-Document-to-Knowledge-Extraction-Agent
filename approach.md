# Paper Trail — Project Approach

## A. Problem Scoping

### Problem

Enterprise organisations maintain invoices, contracts, and reports containing important business information. Extracting text through OCR or PDF parsers does not establish which entities the text refers to, how they are related, or whether a statement is explicit, conditional, or uncertain.

Paper Trail aims to convert these documents into structured, evidence-backed knowledge. It must avoid unsupported claims and incorrect links to existing master records.

### Objectives

- Extract entities, relationships, and business facts.
- Preserve evidence and document provenance.
- Resolve entities against master records only when justified.
- Flag ambiguity and unsupported claims for review.
- Evaluate correctness, reliability, latency, and cost.

### Initial Scope

The initial domain is procurement and contracts. Inputs include digital and scanned PDFs. Synthetic documents and synthetic master records will be used during early development.

The prototype will not make autonomous legal decisions, execute payments, or modify authoritative enterprise records without an approved workflow.

## B. System Design

### Proposed Architecture

The system will use a modular processing pipeline:

1. **Document ingestion:** Validate uploaded files and assign a document identifier.
2. **Text extraction:** Extract digital PDF text and use OCR for scanned pages.
3. **Preprocessing:** Preserve page references and organise text into manageable sections.
4. **Knowledge extraction:** Identify entities, relationships, facts, dates, amounts, and conditional clauses.
5. **Schema validation:** Check output types, required fields, and allowed entity and relationship types.
6. **Evidence validation:** Verify that each extracted claim is supported by its cited source text.
7. **Entity resolution:** Generate candidate master-record matches and assess whether available evidence supports a link.
8. **Human review:** Route ambiguous matches, contradictions, and unsupported claims for review.
9. **Storage and presentation:** Save structured results and expose evidence, status, and errors for inspection.
10. **Evaluation and observability:** Measure accuracy, failures, latency, and cost across versioned test runs.

### Model-versus-Code Decisions

A language model may be used to identify and interpret facts from document text. Deterministic application code should handle file validation, schema enforcement, identifier management, logging, evaluation calculations, and access controls.

Entity linking should combine available structured evidence, such as identifiers and verified attributes, with candidate generation where useful. A language model's confidence statement alone is not sufficient proof of a match.

The model provider and extraction implementation will remain replaceable until provider restrictions, privacy requirements, and evaluation results are known.

### Human Approval and Failure Paths

- Ambiguous entity matches remain unresolved until reviewed.
- Unsupported or contradictory claims are flagged.
- Invalid model output is retried only under a controlled policy; persistent failures are reported.
- Unreadable documents and OCR failures are surfaced to the user.
- Reviewer decisions are recorded where review functionality is implemented.
- No uncertain link is silently converted into an authoritative master-record relationship.

### Workflow Diagram

The repository's architecture diagram should represent this flow:

Document upload → text extraction/OCR → fact and relationship extraction → schema validation → evidence validation → entity resolution → accepted result or human review → structured storage and evaluation.

Failure branches should include unreadable documents, invalid output, missing evidence, conflicting facts, and ambiguous entity matches.

## C. Data and Knowledge

### Data Strategy

The initial dataset will use synthetic invoices, contract excerpts, and vendor master records. Each evaluation example will have a documented expected output covering entities, relationships, evidence, conditions, and linking decisions.

Development and held-out evaluation data will be stored separately. Data sources, licences, generation methods, and dataset versions will be documented.

### Initial Semantic Model

The proposed model includes:

- **Vendor:** Identifier, legal name, aliases, and available business attributes.
- **Invoice:** Invoice identifier, vendor reference, date, amount, and currency.
- **Contract:** Contract identifier, parties, effective date, and relevant terms.
- **PaymentTerm:** Payment conditions extracted from source documents.
- **RenewalClause:** Renewal conditions, required consent, term information when available, and supporting evidence.

Relationships will include `issued_by`, `references`, `contains_clause`, and other relationships supported by the data.

Extracted mentions and resolved master entities must be distinguishable. Unresolved entities must remain representable without inventing a master-record identifier.

### Required Challenge Cases

1. **Ambiguous vendor:** An invoice names "Sri Venkateswara Traders Pvt Ltd", while master records contain "Sri Venkateshwara Traders" and "SV Traders". The system must not force a link without sufficient evidence.
2. **Conditional renewal:** A contract says it "may be renewed for a further term by mutual consent of the parties". The system must extract a conditional renewal clause rather than assert that renewal has occurred or is guaranteed.

## D. Evaluation Framework

The evaluation runner will compare system outputs with manually verified expected outputs.

Metrics will include:

- Entity and relationship precision, recall, and F1.
- Evidence-support accuracy.
- Entity-linking precision and candidate ranking quality.
- Correct interpretation of conditional obligations.
- Appropriate abstention and human hand-off.
- Schema validity and processing failure rate.
- Latency, cost per request, and reviewer correction rate.

Tests will include clear examples, ambiguous names, missing evidence, contradictory statements, OCR errors, and conditional language. Evaluation examples will remain separate from prompt development and model tuning.

Quantitative pass thresholds will be defined before final evaluation. Every prototype will be evaluated using recorded configurations and versioned test data. Results will report the sample size, conditions, metrics, and known limitations.

## E. Risks and Responsibility

### Key Risks

- Hallucinated facts or relationships.
- Incorrect links between similar vendor names.
- Misinterpretation of obligations or conditional language.
- OCR errors that change names, amounts, or dates.
- Sensitive information exposed through external APIs or logs.
- Evaluation leakage, biased examples, and misleading aggregate metrics.
- Uncontrolled model usage or hosting costs.

### Mitigations

- Require source evidence for extracted claims.
- Validate structured outputs programmatically.
- Use conservative linking and explicit abstention.
- Route ambiguous cases to human review.
- Use synthetic data initially and seek approval before processing sensitive information.
- Keep secrets out of version control and minimise sensitive logging.
- Separate development and evaluation datasets.
- Measure actual performance and disclose limitations.

### Open Decisions

Model-provider approval, deployment restrictions, expected document volume, retention rules, quantitative evaluation thresholds, and the review process remain subject to confirmation by the capstone organisers.

This document describes the proposed approach. Implemented features and measured outcomes will be recorded in `ARTEFACT.md` as development progresses.