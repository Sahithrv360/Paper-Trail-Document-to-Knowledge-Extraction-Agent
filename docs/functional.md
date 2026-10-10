# Functional Requirements — Paper Trail

## 1. Project Overview

Paper Trail is a Document-to-Knowledge Extraction Agent designed to convert unstructured enterprise documents into structured, evidence-backed information. The initial domain is procurement and contracts, with support for invoices, contract documents, and related business records.

The system goes beyond text extraction by identifying entities, relationships, business facts, and conditional obligations. It compares extracted entities with existing master records and avoids creating links when the available evidence is insufficient.

## 2. Objectives

- Extract relevant entities, relationships, and business facts from enterprise documents.
- Preserve evidence supporting every extracted fact.
- Link extracted entities to existing master records only when sufficient evidence is available.
- Identify uncertainty, ambiguity, and unsupported claims.
- Route uncertain cases to human review.
- Produce structured, validated output that can be inspected and evaluated.
- Record processing outcomes, errors, latency, and evaluation results.

## 3. Users and Use Cases

### Primary users

- Procurement staff reviewing invoices and vendor information.
- Contract reviewers inspecting clauses and obligations.
- Business analysts searching extracted enterprise knowledge.
- Project evaluators assessing extraction accuracy and reliability.

### Main use cases

1. Upload an invoice or contract in PDF format.
2. Extract text from digital or scanned documents.
3. Identify entities, relationships, amounts, dates, and relevant clauses.
4. Inspect the evidence supporting an extracted fact.
5. Compare extracted entities against vendor or contract master records.
6. Review ambiguous or unsupported matches.
7. View, export, and evaluate structured results.

## 4. Functional Requirements

### FR-01: Document Input

The system shall accept supported PDF documents and validate file type, size, and readability before processing.

### FR-02: Text Extraction

The system shall extract text from digital PDFs and use OCR for scanned PDFs when required. Extraction failures and unreadable pages shall be reported.

### FR-03: Entity Extraction

The system shall identify relevant entities, including vendors, invoices, contracts, dates, monetary amounts, payment terms, and renewal clauses.

### FR-04: Relationship Extraction

The system shall identify supported relationships between entities, such as an invoice being issued by a vendor or a contract containing a renewal clause.

### FR-05: Business Fact Extraction

The system shall extract relevant business facts while preserving distinctions between explicit statements, conditional statements, interpretations, and unknown information.

### FR-06: Evidence Provenance

Each extracted fact shall include supporting source text and, where available, a document identifier, page number, and source location.

### FR-07: Evidence Validation

The system shall check whether the cited evidence supports the extracted fact. Unsupported or contradictory claims shall be flagged rather than silently accepted.

### FR-08: Master-Record Linking

The system shall compare extracted entities with available master records and provide candidate matches with supporting reasons. A link shall only be accepted automatically when the matching policy and evidence justify it.

### FR-09: Ambiguity Handling

When multiple candidates are plausible or evidence is insufficient, the system shall leave the entity unresolved and request human review.

### FR-10: Conditional Obligations

The system shall preserve conditional and modal language. For example, a clause stating that a contract "may be renewed by mutual consent" shall not be interpreted as proof that renewal has occurred or is guaranteed.

### FR-11: Structured Output

The system shall produce output conforming to a documented schema. Invalid output shall be rejected, corrected through a controlled retry when appropriate, or sent for review.

### FR-12: Review and Correction

A reviewer shall be able to inspect evidence, candidate matches, and flagged results. Where implemented, reviewer decisions and corrections shall be recorded for audit and evaluation.

### FR-13: Results and Export

The system shall display processed facts and their evidence. Export formats and query capabilities shall be determined during implementation planning.

### FR-14: Observability

The system shall record processing status, errors, latency, model usage, estimated cost where measurable, and evaluation metrics where applicable. Sensitive document content shall not be included in logs by default.

## 5. Non-Functional Requirements

- **Reliability:** Processing failures shall be reported clearly.
- **Traceability:** Extracted facts shall be traceable to source evidence.
- **Privacy:** Sensitive data shall not be sent to external services without appropriate approval.
- **Security:** API keys and credentials shall be stored outside source control.
- **Maintainability:** Extraction, validation, linking, and storage components should be modular.
- **Reproducibility:** Dataset versions, prompts, model configuration, and evaluation results should be recorded.
- **Performance:** Latency and resource use shall be measured against agreed test conditions.
- **Extensibility:** New document types and entity types should be addable without redesigning the complete pipeline.

## 6. Acceptance Criteria

The initial prototype shall be tested against the following behaviours:

1. A readable digital PDF can be processed into structured output.
2. A scanned PDF can be processed through OCR, or a clear error is returned if OCR fails.
3. Extracted facts contain source evidence and available page references.
4. Output is checked against the documented schema.
5. An ambiguous vendor name is not forcibly linked to a master record.
6. A conditional renewal clause is not reported as a completed or guaranteed renewal.
7. Unsupported claims are flagged or omitted.
8. Processing errors are recorded without exposing secrets or unnecessary sensitive content.
9. The evaluation runner can compare system outputs with labelled expected outputs.
10. Results can be reproduced using documented setup instructions and versioned test data.

Quantitative accuracy, latency, and cost thresholds shall be defined before final evaluation and reported with the test conditions.

## 7. Out of Scope for the Initial Prototype

- Autonomous legal decisions or contract approval.
- Automatic payment execution.
- Guaranteed resolution of every entity-matching ambiguity.
- Processing every possible enterprise document format or language.
