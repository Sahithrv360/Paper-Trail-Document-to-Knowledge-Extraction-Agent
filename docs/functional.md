# Functional Document — Paper Trail
## Document-to-Knowledge Extraction Agent

### 1. Problem Understanding

Organisations manage large volumes of business documents, including purchase invoices, procurement records, contracts, agreements, and payment terms. These documents contain important information about vendors, transactions, obligations, dates, amounts, and relationships between business entities.

Although text extraction and OCR can convert documents into machine-readable text, the extracted text does not automatically become reliable, structured business knowledge. Information may be incomplete, inconsistently named, spread across multiple pages, or expressed through conditional language.

For example, a vendor may appear under slightly different names in an invoice and a master database. A contract may state that an agreement *may be renewed subject to mutual consent*, which must not be interpreted as confirmation that renewal has occurred.

The problem is therefore not only document reading. It is the reliable conversion of unstructured information into structured, traceable, and appropriately qualified knowledge.

### 2. Domain Context and Current-Process Challenges

The initial domain is procurement and contract management.

Typical documents include:
- Purchase invoices and vendor invoices.
- Procurement and purchase-order documents.
- Contracts and service agreements.
- Payment terms and contractual obligations.
- Renewal clauses and other conditional provisions.

The current process may involve manually reading documents, copying information into spreadsheets or enterprise systems, comparing names against master records, and reviewing discrepancies.

This creates several challenges:

1. **Unstructured information:** Relevant facts are embedded in paragraphs, tables, scanned pages, and different document layouts.
2. **Inconsistent entity names:** Vendors may use abbreviations, spelling variations, or legal-name variations across documents and master records.
3. **Missing evidence:** Extracted values may be difficult to verify without locating their original source.
4. **Conditional language:** Contractual conditions can be incorrectly represented as completed actions or guaranteed outcomes.
5. **Uncertainty:** Similar names or incomplete information can lead to incorrect record linking.
6. **Limited reproducibility:** Without a consistent evaluation process, it is difficult to determine whether a system's performance is improving.

The proposed system addresses these challenges while retaining the original document as the source of truth.

### 3. Proposed Intervention

Paper Trail is a document-to-knowledge extraction agent that processes enterprise documents, extracts structured business facts, attaches supporting evidence, and links extracted entities to existing master records when the evidence is sufficient.

The system is designed around the following capabilities:

- **Document ingestion:** Accept digital and scanned PDF documents.
- **Text extraction:** Use PDF parsing and OCR where appropriate, retaining page-level references.
- **Knowledge extraction:** Identify entities, relationships, dates, amounts, payment terms, contractual conditions, and other relevant facts.
- **Evidence provenance:** Associate extracted facts with supporting text, document identifiers, and page numbers.
- **Schema validation:** Check that extracted results conform to the agreed structured representation.
- **Master-record linking:** Generate and evaluate candidate matches against synthetic vendor and other business records.
- **Uncertainty handling:** Leave ambiguous matches unresolved or route them for human review.
- **Inspection and evaluation:** Provide a way to inspect results, review evidence, and measure extraction and linking quality.

The initial prototype will use synthetic procurement and contract data. Model and service choices will remain configurable until provider, privacy, and deployment constraints are confirmed.

### 4. High-Level User Journey

1. **Submit a document:** A user uploads a procurement or contract PDF.
2. **Extract the content:** The system identifies digitally available text or applies OCR to scanned pages.
3. **Identify business knowledge:** The extraction component identifies entities, relationships, facts, and contractual conditions.
4. **Attach evidence:** Each extracted fact is associated with its supporting passage and source page.
5. **Validate the output:** The system checks the structured result and flags missing, invalid, or unsupported information.
6. **Evaluate record matches:** Extracted entities are compared with the available semantic model and master records.
7. **Handle uncertainty:** When the evidence does not support a sufficiently reliable match, the system leaves the link unresolved or requests human review.
8. **Inspect the results:** The user reviews extracted facts, source evidence, candidate matches, confidence information, and warnings.
9. **Evaluate performance:** Test results are compared against expected outputs, and quality, latency, cost, and failure measurements are recorded.

The system supports human review rather than treating every model output as an authoritative business decision.

### 5. Technical and Agentic Construct

The proposed architecture separates deterministic processing and validation from model-assisted interpretation.

**A. Document ingestion layer**

Accepts PDF files, validates inputs, extracts digital text, and invokes OCR for scanned content. It preserves document identifiers and page references for downstream provenance.

**B. Knowledge extraction layer**

Uses a configurable extraction model or approach to identify entities, relationships, business facts, and conditional obligations. Structured output is validated against a predefined schema.

**C. Evidence and validation layer**

Checks that extracted claims have supporting evidence, retains source references, and flags unsupported or malformed results. Deterministic code handles schema checks and other rules that should not be delegated solely to a language model.

**D. Semantic model and record-linking layer**

Defines the relevant business entities and their relationships. It compares extracted entities with master records, returns candidate matches with supporting reasons, and avoids creating unsupported links.

**E. Human review and results layer**

Presents extracted information, evidence, warnings, and unresolved matches for inspection. Review outcomes can be recorded for correction and evaluation.

**F. Evaluation and observability layer**

Runs repeatable test cases and records quality metrics, schema validity, latency, cost where measurable, and failure rates. Prototype versions and prompt configurations are tracked so that changes can be compared.

#### Agent and code responsibilities

Model-assisted components are used for interpreting document language and extracting structured information. Conventional code is used for file validation, schema enforcement, evidence-reference checks, record-matching rules, persistence, access controls, and metric calculation.

The system does not rely on an unconstrained autonomous agent to make final business-record decisions. Unsupported claims and ambiguous links must be flagged rather than silently accepted.

### 6. Expected Behaviour and Acceptance Criteria

**Ambiguous vendor identity**

Given an invoice containing “Sri Venkateswara Traders Pvt Ltd” and master records containing “Sri Venkateshwara Traders” and “SV Traders”, the system should preserve the extracted name and its evidence. It must not automatically assert that either master record is the correct identity without sufficient supporting evidence.

**Conditional renewal**

Given the clause “may be renewed for a further term by mutual consent of the parties”, the system should represent renewal as conditional. It must not claim that the contract has already been renewed or that renewal is guaranteed.

**Evidence traceability**

A reviewer should be able to identify the source document, page, and supporting passage for an extracted fact.

**Structured output**

The system should validate its outputs against the agreed schema and report invalid or incomplete results.

**Evaluation**

The team should evaluate extraction, evidence support, record linking, uncertainty handling, and technical reliability using a repeatable test set. Actual measurements must be reported separately from target values.

### 7. Scope, Data, and Limitations

The initial scope covers procurement and contract documents, including digital PDFs and scanned PDFs. Synthetic data will be used during initial development and evaluation.

The prototype will focus on document understanding, evidence-backed extraction, semantic linking, human review, and measurable evaluation. Production integration with enterprise systems, unrestricted autonomous decisions, and processing of confidential organisational data are outside the initial scope unless explicitly approved.

Potential limitations include poor scan quality, OCR errors, unusual document layouts, ambiguous entity names, incomplete master data, and model errors in interpreting complex clauses.

The team will document these limitations and evaluate representative failure cases. Sensitive documents will not be sent to external model services without approval.

### 8. Success Criteria

The project will be evaluated using:
- Entity and relationship precision, recall, and F1.
- Evidence-support accuracy.
- Master-record linking precision and candidate-ranking quality.
- Correct treatment of conditional obligations.
- Schema-validity and failure rates.
- Latency and cost per request, where measurable.
- Human-review corrections and unresolved cases.

Numerical targets will be defined before final evaluation. Results will be reported using the actual test-set size and measured system performance.

The intended outcome is a working, inspectable prototype that transforms unstructured business documents into structured knowledge while preserving evidence, representing uncertainty, and avoiding unsupported conclusions.