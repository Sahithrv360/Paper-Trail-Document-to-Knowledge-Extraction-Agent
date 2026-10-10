# Paper Trail — Artefact and Evaluation Record

## 1. Project Status

**Project:** Paper Trail — Document-to-Knowledge Extraction Agent  
**Domain:** Procurement and contracts  
**Status:** Initial repository and documentation stage  
**Default branch:** `main`

This document records implemented components, prototype versions, data provenance, evaluation results, limitations, and deployment status.

At the initial documentation stage, the existence of a repository or architecture image does not imply that the extraction pipeline has been implemented or evaluated.

## 2. What Is Built and What Remains

### Repository Baseline

- [x] Initial GitHub repository created.
- [x] Initial architecture image added.
- [ ] Functional requirements completed and reviewed.
- [ ] Proposed approach documented.
- [ ] Synthetic dataset and expected outputs created.
- [ ] PDF text extraction implemented.
- [ ] OCR fallback implemented.
- [ ] Entity and relationship extraction implemented.
- [ ] Evidence provenance and validation implemented.
- [ ] Master-record candidate matching implemented.
- [ ] Human-review workflow implemented.
- [ ] Evaluation runner and versioned results implemented.
- [ ] Observability and public deployment completed.

Checkboxes should be updated only when the corresponding work has been completed and verified.

## 3. Prototype History

Maintain one entry for every meaningful prototype. Create a Git tag for each reproducible end-to-end version.

| Version | Scope | Evaluation | Outcome |
|---|---|---|---|
| P0 | Initial repository and documentation | Not run | Baseline established |
| P1 | First end-to-end document-processing slice | Pending | To be recorded |
| P2 | Evidence validation and entity resolution | Pending | To be recorded |
| P3 | Review workflow and observability | Pending | To be recorded |
| Final | Evaluated release candidate | Pending | To be recorded |

These are planned milestones, not claims that the prototypes exist. Update the table with actual tag names, dates, commits, changes, and results.

## 4. Data Provenance

Record the origin and handling of every dataset used.

| Dataset | Source and licence | Purpose | Status |
|---|---|---|---|
| Synthetic invoices | To be documented | Development and evaluation | Not yet created |
| Synthetic contracts | To be documented | Development and evaluation | Not yet created |
| Synthetic vendor master | To be documented | Entity-resolution testing | Not yet created |
| Held-out evaluation set | To be documented | Final or independent evaluation | Not yet created |

For generated data, record the generation method, any templates or source material, dataset version, and the procedure used to verify expected answers.

Do not include real personal or confidential data without appropriate consent and approval. Keep sensitive source documents out of public version control.

## 5. Semantic Model and Linking Decisions

The proposed initial entities are Vendor, Invoice, Contract, PaymentTerm, and RenewalClause.

The proposed relationships include `issued_by`, `references`, and `contains_clause`.

Record the final schema, identifier policy, aliases, candidate-generation method, linking thresholds, and abstention rules when implemented.

### Required Challenge Cases

**Ambiguous vendor names**

Expected behaviour: preserve the extracted vendor mention, show plausible master-record candidates, explain the available evidence, and leave the link unresolved when the evidence is insufficient.

**Conditional renewal**

Expected behaviour: preserve the conditional meaning of the clause and its source evidence. Do not assert that renewal has occurred or is guaranteed.

Record the actual output, expected output, pass/fail decision, and any corrective change for each case.

## 6. Prompt Pack and Model Configuration

Maintain version-controlled prompts separately from application code where practical.

For each evaluated configuration, record:

- Prompt identifier and version.
- Model provider and model identifier.
- Relevant parameters and schema version.
- Input preprocessing configuration.
- Retry and validation policy.
- Evaluation dataset version.
- Changes from the previous version.

Do not commit API keys, access tokens, or confidential document contents. Model configuration must be reproducible to the extent supported by the provider.

## 7. Evaluation Results

No measured results are available at the initial documentation stage.

Record results from the running system, not estimates or manually invented values.

| Metric | Definition | Result |
|---|---|---|
| Entity extraction F1 | Entity precision and recall combined | Pending |
| Relationship extraction F1 | Relationship precision and recall combined | Pending |
| Evidence-support accuracy | Proportion of evaluated claims supported by their cited evidence | Pending |
| Link precision | Proportion of accepted links that are correct | Pending |
| Candidate ranking | Quality of ranking against labelled candidates | Pending |
| Conditional interpretation | Correct handling of conditional or modal statements | Pending |
| Schema validity | Proportion of outputs satisfying the required schema | Pending |
| Abstention / hand-off | Correct handling of cases that should remain unresolved | Pending |
| Latency | Measured processing time under stated conditions | Pending |
| Cost per request | Measured or estimated cost with calculation method stated | Pending |
| Failure rate | Proportion of requests that fail under the stated test conditions | Pending |
| Reviewer correction rate | Proportion of reviewed outputs requiring correction | Pending |

For every evaluation, record the run date, prototype tag, sample size, dataset version, model configuration, thresholds, errors, and known limitations.

## 8. Deployment and Observability

**Deployment URL:** Not yet available  
**Observability dashboard:** Not yet available

When deployed, document the public URL, access controls, configuration, and limitations of the free-tier environment.

The observability view should expose appropriate operational metrics, including request count, success and failure rates, latency, token usage or equivalent usage measurements, estimated cost, and evaluation outcomes.

Sensitive document contents, secrets, and unnecessary personal information must not appear in operational logs.

## 9. Limitations and Uncovered Cases

At the initial stage, the system has not been validated against a representative enterprise dataset.

Planned limitations to investigate include:

- Low-quality scans and OCR errors.
- Tables, multi-column layouts, and long documents.
- Conflicting or missing information.
- Similar vendor names and incomplete master records.
- Conditional, negated, and time-dependent statements.
- Unsupported languages and document types.
- Model nondeterminism, latency, and provider availability.
- Privacy and retention constraints of the deployment platform.

Update this section with observed failures and cases that have not been tested.

## 10. Team Ownership

Record the actual allocation of responsibilities and contributions.

| Area | Owner | Contribution / status |
|---|---|---|
| Requirements and documentation | To be assigned | Pending |
| Document processing and OCR | To be assigned | Pending |
| Extraction and evidence validation | To be assigned | Pending |
| Entity resolution and semantic model | To be assigned | Pending |
| Evaluation and observability | To be assigned | Pending |
| Interface and deployment | To be assigned | Pending |

Update ownership to reflect the team's real contributions and avoid claiming work that has not been completed.