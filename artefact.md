# Paper Trail — Artefact and Evaluation Record

## 1. Project Status

**Project:** Paper Trail — Document-to-Knowledge Extraction Agent  
**Domain:** Procurement and contracts  
**Status:** A local image-to-graph slice is implemented; real invoice accuracy, OCR/PDF fallback, RAG benefit, evidence review, and UI remain unverified or incomplete.
**Default branch:** `main`

This document records implemented components, prototype versions, data provenance, evaluation results, limitations, and deployment status.

The repository has a watcher-to-Donut-to-SQLite-graph path for JPG/PNG images, explicit validation/review statuses, and 47 passing unit tests. This does not establish that the real model extracts invoices accurately. PDF OCR and a frontend are absent.

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
- [x] Invoice schema, Donut adapter, dataset inspector, and small baseline evaluator implemented.
- [x] Initial baseline run recorded against the official test split.
- [ ] Broader repeatable evaluation and failure-case review completed.
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
| Katana ML invoice dataset | [Hugging Face dataset card (MIT)](https://huggingface.co/datasets/katanaml-org/invoices-donut-data-v1); original Mendeley source is [CC BY 4.0](https://data.mendeley.com/datasets/tnj49gpmtz) | Invoice schema inspection and Donut baseline | 425 train / 50 validation / 26 test; see `data/README.md` |

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

### Initial Donut Invoice Baseline — 2026-10-18

The run used 5 evenly spaced records from the official test split (indices `0, 6, 12, 19, 25`) with CPU inference. Dataset revision: `d2cde298e79c94fb05bc320999deb4b7889b0464`. Model: `katanaml-org/invoices-donut-model-v1` at revision `1745f5a29c37bc5d2e16ab5e2bc6aba57fdd6f7c`. Runtime: Python 3.12.10, PyTorch 2.14.1, Transformers 4.57.6.

The model matched 28 of 75 annotated field values (37.3% micro exact-match accuracy); no invoice was fully exact (0/5). Invoice number, invoice date, and customer tax ID each scored 5/5. Subtotal scored 4/5; vendor name, vendor tax ID, IBAN, and tax amount each scored 2/5; total amount scored 1/5. Customer name and all five line-item fields scored 0/5 exact match. The generated predictions were present for fewer than all rows on customer name and total amount (coverage 2/5); line-item fields had 3/5 coverage.

This is a five-document compatibility baseline, not a production result. The model card reports 0.96 accuracy without defining the metric or split protocol, and the model is associated with this dataset. Do not claim independent generalization from this run. See `reports/donut-baseline.json` for the machine-readable metrics; the report is ignored by Git and can be regenerated with the documented command.

Record results from the running system, not estimates or manually invented values.

| Metric | Definition | Result |
|---|---|---|
| Entity extraction F1 | Entity precision and recall combined | Pending; the initial Donut run reports field exact match rather than entity F1 |
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

The system has not been validated against a representative enterprise dataset. The invoice baseline covers five documents from one small, programmatically generated dataset.

Observed baseline failure: invoice number and date were exact on the five sampled records, while all line-item fields had zero exact matches. Customer-name output was available for only two of five records. Review field-level failures on a larger, representative sample before attempting fine-tuning.

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
