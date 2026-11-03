"""Local and global GraphRAG retrieval that returns traceable graph evidence."""

import re

from kg_agent.models import Evidence


def graph_search(store, seed_ids: list[str], *, hops: int = 2,
                 rel_types: list[str] | None = None, max_nodes: int = 80,
                 limit: int = 20) -> list[Evidence]:
    if not seed_ids or limit <= 0:
        return []
    graph = store.subgraph(seed_ids, hops=hops, rel_types=rel_types,
                           max_nodes=max_nodes)
    nodes = {node.id: node for node in graph.nodes}
    evidence = []
    for edge in graph.edges:
        source, target = nodes[edge.source], nodes[edge.target]
        attrs = ", ".join(f"{key}={value}" for key, value in edge.properties.items())
        triple = f"({source.name}) -[{edge.type}]-> ({target.name})"
        if attrs:
            triple += f" {{{attrs}}}"
        chunks = store.chunks_for([edge.id, edge.source, edge.target])
        evidence.append(Evidence(id=f"graph:{edge.id}", source_tool="graph", kind="triple",
            text=triple, score=1.0, doc_id=chunks[0].doc_id if chunks else None,
            page=chunks[0].page if chunks else None, node_ids=[edge.source, edge.target],
            edge_ids=[edge.id], chunk_ids=[chunk.id for chunk in chunks]))
        if len(evidence) >= limit:
            break
    return evidence


def graph_global_search(store, query: str, *, limit: int = 5, embedder=None) -> list[Evidence]:
    """Find relevant whole-graph communities with deterministic summary text."""
    communities = store.communities()
    if not communities or limit <= 0:
        return []
    summaries = []
    node_lookup = {node.id: node for node in store.all_nodes()}
    for community in communities:
        members = [node_lookup[node_id] for node_id in community.node_ids]
        text = "; ".join(f"{node.type}: {node.name} ({node.description})" for node in members)
        summaries.append((community, text))
    if embedder:
        query_vector = embedder.embed([query])[0]
        vectors = embedder.embed([text for _, text in summaries])
        def similarity(vector):
            import math
            denom = math.sqrt(sum(a*a for a in vector) * sum(b*b for b in query_vector))
            return sum(a*b for a, b in zip(vector, query_vector)) / denom if denom else 0
        ranked = sorted(((similarity(vector), pair) for vector, pair in zip(vectors, summaries)),
                        key=lambda row: -row[0])
    else:
        terms = set(query.casefold().split())
        ranked = [(len(terms & set(text.casefold().split())) / max(1, len(terms)), pair)
                  for pair in summaries]
        ranked.sort(key=lambda row: -row[0])
    return [Evidence(id=f"community:{community.id}", source_tool="graph",
                     kind="community_summary", text=text, score=score,
                     node_ids=community.node_ids,
                     chunk_ids=[cid for node_id in community.node_ids
                                for cid in node_lookup[node_id].chunk_ids])
            for score, (community, text) in ranked[:limit]]


def deterministic_aggregation(store, question: str) -> list[Evidence]:
    """Compute supported count/sum questions directly from graph properties."""
    lower = question.casefold()
    entity_terms = {"invoices": "Invoice", "invoice": "Invoice", "contracts": "Contract",
                    "contract": "Contract", "suppliers": "Supplier", "supplier": "Supplier",
                    "customers": "Customer", "customer": "Customer", "items": "Item",
                    "products": "Item", "product": "Item"}
    entity_type = next((entity_terms[word] for word in sorted(entity_terms, key=len, reverse=True)
                        if re.search(rf"\b{re.escape(word)}\b", lower)), None)
    if not entity_type:
        return []
    nodes = [node for node in store.all_nodes() if node.type == entity_type]
    if "sum" in lower or "total" in lower:
        property_name = ("contract_value" if entity_type == "Contract" else
                         "total" if entity_type == "Invoice" else None)
        if not property_name:
            return []
        values = [(node, node.properties.get(property_name)) for node in nodes]
        values = [(node, value) for node, value in values
                  if isinstance(value, (int, float))]
        if not values:
            return []
        total = sum(value for _, value in values)
        detail = f"Sum of {property_name} for {entity_type}: {total} USD across {len(values)} records."
        nodes = [node for node, _ in values]
    elif "how many" in lower or "count" in lower or "number of" in lower:
        detail = f"Count of {entity_type}: {len(nodes)} records."
    else:
        return []
    chunks = sorted({chunk_id for node in nodes for chunk_id in node.chunk_ids})
    return [Evidence(id=f"aggregate:{entity_type}", source_tool="graph", kind="entity",
        text=detail, score=1.0, node_ids=[node.id for node in nodes], chunk_ids=chunks)]
