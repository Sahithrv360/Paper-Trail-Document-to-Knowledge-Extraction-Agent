"""Local and global GraphRAG retrieval that returns traceable graph evidence."""

import re

from kg_agent.models import Evidence


_QUESTION_STOPWORDS = {
    "who", "which", "what", "all", "have", "has", "had", "did", "does", "do",
    "the", "a", "an", "for", "of", "to", "with", "by", "customer", "customers",
    "buyer", "buyers", "vendor", "vendors", "seller", "sellers", "bought", "buy",
    "buys", "buying", "purchase", "purchases", "purchased", "purchasing", "ordered",
    "orders", "sold", "sell", "sells", "selling", "from", "me", "show", "list",
}
_ATTRIBUTE_TERMS = {"handmade", "handknotted", "handwoven"}
_CATEGORY_TERMS = {"rug", "rugs", "carpet", "carpets"}


def _split_catalog_word(word: str, vocabulary: set[str]) -> list[str]:
    """Split a joined question token using words actually present in item descriptions."""
    if word in vocabulary:
        return [word]
    memo = {}

    def visit(position):
        if position == len(word):
            return []
        if position in memo:
            return memo[position]
        candidates = sorted((candidate for candidate in vocabulary
            if len(candidate) >= 3 and word.startswith(candidate, position)),
            key=lambda candidate: (-len(candidate), candidate))
        for candidate in candidates:
            suffix = visit(position + len(candidate))
            if suffix is not None:
                memo[position] = [candidate, *suffix]
                return memo[position]
        memo[position] = None
        return None

    return visit(0) or [word]


def party_product_search(store, question: str, *, limit: int = 100) -> list[Evidence]:
    """Find invoice parties for product questions through LineItem -> Invoice.

    Product terms may be concatenated in the question ("jordansneakers") or split
    differently in OCR text. Evidence retains both relationship edges and invoice status.
    """
    lower = question.casefold()
    buyer = bool(re.search(r"\b(?:bought|buy|buys|buying|purchase|purchases|purchased|"
                           r"purchasing|ordered|orders)\b", lower))
    seller = bool(re.search(r"\b(?:sold|sell|sells|selling)\b", lower)) and not buyer
    if not buyer and not seller:
        return []
    party_type, relation = ("Customer", "BILLED_TO") if buyer else ("Vendor", "ISSUED")
    nodes = {node.id: node for node in store.all_nodes()}
    item_descriptions = [str(node.properties.get("description") or node.name)
                         for node in nodes.values() if node.type == "LineItem"]
    vocabulary = set()
    for description in item_descriptions:
        split = re.sub(r"([a-z])([A-Z])", r"\1 \2", description)
        vocabulary.update(re.findall(r"[a-z0-9]+", split.casefold()))
    raw_terms = re.findall(r"[a-z0-9]+", lower)
    terms = [part for raw in raw_terms if raw not in _QUESTION_STOPWORDS
             for part in _split_catalog_word(raw, vocabulary)]
    if not terms:
        return []
    handmade_requested = any(term in terms for term in ("handmade", "handknotted", "handwoven"))
    silk_requested = "silk" in terms
    rug_requested = any(term in _CATEGORY_TERMS for term in terms)

    edges = list(getattr(store, "edges", []))
    invoice_for_item = {}
    parties_for_invoice = {}
    for edge in edges:
        if edge.type == "CONTAINS":
            left, right = nodes.get(edge.source), nodes.get(edge.target)
            if left and right:
                if left.type == "LineItem" and right.type == "Invoice":
                    invoice_for_item[left.id] = (right.id, edge)
                elif right.type == "LineItem" and left.type == "Invoice":
                    invoice_for_item[right.id] = (left.id, edge)
        elif edge.type == relation:
            left, right = nodes.get(edge.source), nodes.get(edge.target)
            if left and right:
                if left.type == party_type and right.type == "Invoice":
                    parties_for_invoice.setdefault(right.id, []).append((left.id, edge))
                elif right.type == party_type and left.type == "Invoice":
                    parties_for_invoice.setdefault(left.id, []).append((right.id, edge))

    matches = {}
    for item_id, (invoice_id, contains_edge) in invoice_for_item.items():
        item, invoice = nodes[item_id], nodes[invoice_id]
        description = " ".join(str(item.properties.get("description") or item.name).split())
        split_description = re.sub(r"([a-z])([A-Z])", r"\1 \2", description)
        normalized = re.sub(r"[^a-z0-9]+", " ", split_description.casefold())
        compact = normalized.replace(" ", "")
        desc_terms = normalized.split()
        # Match each requested product term against catalog words or compact spellings.
        requested = [term.rstrip("s") for term in terms
                     if term not in _ATTRIBUTE_TERMS and term not in _CATEGORY_TERMS]
        if not all(any(word.rstrip("s").startswith(term) or term.startswith(word.rstrip("s"))
                       for word in desc_terms) or term in compact for term in requested):
            continue
        if silk_requested and "silk" not in compact:
            continue
        if rug_requested and not any(term in compact for term in ("rug", "carpet")):
            continue
        if handmade_requested and not any(term in compact for term in ("handmade", "handknotted", "handwoven")):
            continue
        for party_id, party_edge in parties_for_invoice.get(invoice_id, []):
            party = nodes[party_id]
            invoice_number = str(invoice.properties.get("invoice_number") or invoice.name)
            key = (party_id, invoice_id)
            match = matches.setdefault(key, {"party": party, "invoice": invoice,
                "invoice_number": invoice_number, "descriptions": [], "edge_ids": set(),
                "node_ids": {party_id, invoice_id}})
            if description not in match["descriptions"]:
                match["descriptions"].append(description)
            match["edge_ids"].update((contains_edge.id, party_edge.id))
            match["node_ids"].add(item_id)

    results = []
    for index, match in enumerate(sorted(matches.values(), key=lambda row:
            (row["party"].name.casefold(), row["invoice_number"])), 1):
        review_status = match["invoice"].properties.get("review_status", "unreviewed")
        description = "; ".join(match["descriptions"])
        action = "is billed to" if buyer else "issued"
        text = (f"{party_type} {match['party'].name} {action} invoice "
                f"{match['invoice_number']}, which contains line item {description!r}. "
                f"Review status: {review_status}.")
        results.append(Evidence(id=f"party-product:{index}", source_tool="graph",
            kind="triple", text=text, score=1.0, doc_id=match["invoice"].id,
            node_ids=sorted(match["node_ids"]), edge_ids=sorted(match["edge_ids"])))
    return results[:max(0, limit)]


def buyer_product_search(store, question: str, *, limit: int = 100) -> list[Evidence]:
    """Backward-compatible name for the generic invoice-party product lookup."""
    return party_product_search(store, question, limit=limit)


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
    if re.search(r"\b(sum|total)\b", lower):
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
    elif re.search(r"\b(how many|count|number of)\b", lower):
        detail = f"Count of {entity_type}: {len(nodes)} records."
    else:
        return []
    chunks = sorted({chunk_id for node in nodes for chunk_id in node.chunk_ids})
    return [Evidence(id=f"aggregate:{entity_type}", source_tool="graph", kind="entity",
        text=detail, score=1.0, node_ids=[node.id for node in nodes], chunk_ids=chunks)]
