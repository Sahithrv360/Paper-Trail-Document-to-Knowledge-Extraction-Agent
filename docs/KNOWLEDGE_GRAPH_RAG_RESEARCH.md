# Knowledge Graph, Neo4j, RAG and LLM Research

**Research snapshot:** 28 October 2026. Neo4j releases, model availability, pricing and provider data terms change; verify them before deployment.

## Executive recommendation

Neo4j can support Paper Trail's graph and retrieval needs. The current SQLite ledger should remain the authoritative store for this prototype: it already tracks immutable extraction versions, reviews, evidence-backed claims, shared support, deletion and a deterministic materialized graph. Replacing it with Neo4j now would add a server and migration/operations work without fixing the current RAG gap. If Cypher traversals, concurrent users, graph scale or deployment needs justify it, add Neo4j as a tested graph projection behind a storage interface, preserving the SQLite ledger as the audit/source ledger during the first migration stage.

For RAG, use two deliberately separate flows:

1. **Extraction assistance:** current document OCR/text plus approved format guidance or explicitly permitted examples can help an LLM read the document. Current-page evidence remains authoritative; retrieved facts must never fill missing invoice values. The current `TrainingRetriever` masks values into shapes, but has not been wired to the OCR/API pipeline or evaluated. `compare_rag.py` currently uses an empty query and Donut ignores the context, so it is a no-op control rather than a RAG result.
2. **Question answering over stored knowledge:** retrieve approved graph facts and/or evidence-bearing text, then have an LLM summarize only that context with source citations. This is a separate product feature from invoice extraction and should be added after the user query and access policy are defined.

Do not use an unconstrained LLM to write Cypher or mutate the graph. Continue to validate model output through the existing canonical schema, evidence checks and deterministic ontology claim builder. Graph writes should be application-generated from validated, reviewed records.

## Current project baseline

- `graph_ontology.py` defines `Document`, `Invoice`, `Vendor`, `Customer` and `LineItem`, and the directed `SUPPORTS`, `ISSUED`, `BILLED_TO` and `CONTAINS` relations. Classification is explicitly deferred.
- `ledger.py` stores extraction versions, review history and per-document claims; active claims are materialized into graph nodes and edges. It preserves source/evidence and shared-entity support.
- `graph.py` provides graph synchronization and export. The local review API already exposes graph data.
- `extraction/rag.py` retrieves **training examples only**, masks values into format shapes, and returns record IDs and guidance. The retrieval interface is supporting context, not a source of invoice facts.
- The extraction/provider boundary supports retrieval context, but the live comparison does not yet have OCR text as a retrieval query and Donut cannot consume context. A prompt-capable VLM/LLM provider and validation-set comparison are required before claiming benefit.

## Neo4j fit

Neo4j's property graph maps naturally to the existing ontology. Its official Python driver uses Cypher; Neo4j's official `neo4j-graphrag` package supports vector, vector-plus-Cypher, hybrid, tool and text-to-Cypher retrievers. Neo4j vector indexes are available in Community and Enterprise editions; Community uses list-valued embedding properties, while native `VECTOR` properties require Enterprise/Aura block format. Vector retrieval is approximate nearest-neighbor search, so retrieval quality still needs evaluation.

| Question | Finding for Paper Trail |
|---|---|
| Can Neo4j store this graph? | Yes. Map ontology node types to labels and ontology relations to directed relationship types. Keep stable IDs and per-document support/version metadata. |
| Does Neo4j remove SQLite? | No. The ledger's append-only review/version history and transactional claim-union semantics are project behavior that still need explicit modeling. Initially treat Neo4j as a rebuildable projection, not the authority. |
| Does Neo4j provide RAG? | It provides vector/full-text indexes and retriever integrations. You still choose document chunking, embeddings, metadata filters, prompt policy, LLM, source citations and evaluation. |
| Is the official KG extraction builder suitable for invoices? | Not as the first writer. Neo4j's `SimpleKGPipeline` is documented as experimental and extracts entities/relations from unstructured text. Paper Trail already has a fixed ontology and evidence validation; a deterministic writer from validated records is safer. |
| Community or Aura? | Community can support a local single-instance experiment. Aura is managed but sends/stores graph data in a hosted service; review retention, region, security and cost terms before placing real invoices there. |

Official sources: [Neo4j Python driver](https://neo4j.com/docs/python-manual/current/), [Neo4j GraphRAG Python package](https://neo4j.com/docs/neo4j-graphrag-python/current/), [GraphRAG retrievers](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html), [vector indexes and edition support](https://neo4j.com/docs/cypher-manual/current/indexes/search-indexes/vector-indexes/), [KG Builder limitations](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html), [Neo4j editions](https://neo4j.com/docs/operations-manual/current/introduction/).

## Suggested architecture

```text
Document -> bounded PDF/OCR parser -> text + page/box provenance
         -> retrieval of approved guidance/examples (optional)
         -> prompt-capable LLM/VLM -> canonical JSON
         -> schema + evidence + arithmetic validation
         -> SQLite version/review ledger
         -> deterministic ontology claim builder
         -> SQLite graph (now) / Neo4j projection (optional later)

Question -> intent route
         -> structured graph traversal and/or hybrid text/vector retrieval
         -> approved facts + evidence references
         -> answer LLM -> answer with document/page/field citations
```

The LLM is an extractor or answer composer, not an authority. Extraction results remain `needs_review` until a person approves/corrects them. RAG answer retrieval should default to reviewed support; include unreviewed material only if the UI clearly labels it and the user explicitly requests it. Filter retrieval by active document version and permitted review state before ranking. A deletion or superseding extraction must invalidate old chunks/embeddings from active retrieval.

## Implementation plan

### Phase 1: Make the existing extraction RAG experiment real

1. Use the existing PDF/OCR path to produce current-document text with page and bounding-box references. Avoid embedding sensitive text in logs.
2. Define a `Retriever` protocol returning context snippets, source IDs, split/provenance, and retrieval scores. Keep `TrainingRetriever` as one implementation.
3. Make the RAG corpus explicit and versioned: initially training-set examples with values masked to shapes, plus human-approved formatting/schema guidance. Never index validation/test annotations into the retriever.
4. Wire the retrieved guidance into one prompt-capable structured-output provider. Prompt: use context only for format/task guidance; extract every value from the current document; emit null when unreadable; return quotes/page/box for each value.
5. Run no-context and context arms on the same validation rows with the same OCR, model, prompt, and decoding settings. Record retrieved IDs, field exact/normalized accuracy, evidence support, coverage, abstention, latency and API usage. If no query text is available, fail the RAG arm instead of retrieving arbitrary rows.
6. Keep Donut as the non-RAG baseline. Do not use the held-out test split for prompt/retrieval tuning.

### Phase 2: Add knowledge question answering

1. Start with fixed, reviewed Cypher templates for well-defined queries: invoice totals by vendor/date, invoices for a customer, line items for a source invoice, and provenance lookup. Parameterize values; do not execute model-authored Cypher.
2. Add chunk nodes only if users need natural-language search across clauses or source text. Each chunk needs document ID, version/hash, page, source offsets/geometry, review state, text, embedding model/revision and embedding vector.
3. Combine exact structured graph queries with full-text/vector retrieval. Exact invoice numbers, names and tax IDs are better handled lexically; semantic vector search is useful for descriptions and clause language. Deduplicate/rank results, then traverse graph links to gather the supporting invoice/vendor/document facts.
4. Build a constrained answer prompt that says to answer only from supplied results, abstain when none support the answer, distinguish machine-extracted from reviewed facts, and cite filename/version/page/field/evidence. Render citations as clickable links into the local review UI.
5. Evaluate with a small labeled question set: answer correctness, citation precision/completeness, abstention, retrieval recall@k, and latency. Include adversarial questions where retrieved facts conflict with the current document or records are unreviewed.

### Phase 3: Optional Neo4j projection

1. Add a `GraphProjection` interface so current `GraphStore` behavior remains the default implementation.
2. Build an idempotent Neo4j sync/rebuild adapter from active SQLite claims. Use fixed Cypher statements, parameterized property values and stable IDs; create uniqueness constraints for IDs. Store `document_id`, `version`, `content_hash`, `review_status`, claim support and evidence references.
3. Handle shared party nodes with per-document support relationships/claims. On document removal or supersession, remove only that document's support; only remove a shared node when no active supports remain.
4. Add parity checks comparing normalized Neo4j export with SQLite export, including reviewed-only filtering, correction, supersession, deletion and rebuild. Keep SQLite authoritative until parity is demonstrated and backup/restore and migration procedures exist.
5. Only then add chunk embeddings and Neo4j vector/full-text indexes if the chosen Q&A retrieval benefits from graph-aware traversal. For a simple local scale, do not add embeddings or Neo4j just for the phrase “GraphRAG.”

## LLM and privacy choices

- The repository already has an explicit remote-transfer opt-in for the Anthropic vision provider. Preserve this opt-in boundary and keep secrets in environment variables.
- Local OCR plus hosted LLM text can reduce image transfer but still sends invoice content; it is not a privacy boundary. Prefer local models only if hardware and measured extraction quality are adequate.
- Treat invoices and contracts as untrusted input: ignore instructions inside documents, delimit retrieved text, restrict the output schema, and never grant the model database credentials or arbitrary query execution.
- Record provider/model/prompt version, retrieval IDs, input/output token use and latency where returned. Do not store raw prompt/document copies beyond the existing retention policy.
- Before choosing a hosted LLM, check current vendor retention, training use, residency, access controls, pricing and contractual terms for the actual account tier.

## Decision

**Proceed now:** improve the validation-only RAG experiment using OCR text and a prompt-capable provider; preserve SQLite and deterministic graph writes.

**Defer Neo4j:** revisit after a concrete graph-question workload or deployment requirement exists. It is technically compatible and could later supply graph traversal plus vector/full-text retrieval, but it does not make extraction evidence-backed by itself and it does not replace the ledger/review policies.

**Do not claim:** RAG improves extraction, GraphRAG is implemented, or Neo4j is required until measured validation evidence and an operational need exist.
