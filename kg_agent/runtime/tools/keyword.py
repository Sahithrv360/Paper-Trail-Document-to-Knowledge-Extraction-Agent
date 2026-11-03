"""Small dependency-free BM25 search over document chunks and entity names."""

import math
import re

from kg_agent.models import Evidence

STOP_WORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "did", "do",
              "for", "from", "how", "i", "in", "is", "it", "me", "of", "on",
              "or", "the", "this", "to", "was", "what", "which", "who", "with"}


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[\w@.-]+", text.casefold())
            if token not in STOP_WORDS]


def keyword_search(store, query: str, limit: int = 8) -> list[Evidence]:
    if not query.strip() or limit <= 0:
        return []
    docs = []
    for chunk in store.all_chunks():
        docs.append((chunk.id, chunk.text, chunk.doc_id, chunk.page, [], [], [chunk.id]))
    for node in store.all_nodes():
        text = " ".join([node.name, *node.aliases, node.description,
                         *(f"{key} {value}" for key, value in node.properties.items())])
        docs.append((node.id, text, None, None, [node.id], [], node.chunk_ids))
    if not docs:
        return []
    query_terms = _tokens(query)
    tokenized = [_tokens(row[1]) for row in docs]
    avg_len = sum(map(len, tokenized)) / len(tokenized) or 1
    doc_freq = {term: sum(term in set(tokens) for tokens in tokenized)
                for term in set(query_terms)}
    quoted = re.findall(r'["\u201c]([^"\u201d]+)["\u201d]', query)
    results = []
    for row, tokens in zip(docs, tokenized):
        tf = {term: tokens.count(term) for term in set(query_terms)}
        score = 0.0
        for term, count in tf.items():
            if count:
                idf = math.log(1 + (len(docs) - doc_freq[term] + 0.5) /
                               (doc_freq[term] + 0.5))
                score += idf * count * 2.2 / (count + 1.2 *
                          (0.25 + 0.75 * len(tokens) / avg_len))
        text = row[1]
        score += sum(2.0 for phrase in quoted if phrase.casefold() in text.casefold())
        # Exact entity IDs and aliases should outrank broad lexical matches.
        if row[0].casefold() in query.casefold() or any(
                alias.casefold() in query.casefold() for alias in
                (store.node_by_id[row[0]].aliases if row[0] in store.node_by_id else [])):
            score += 8.0
        if score > 0:
            results.append(Evidence(id=f"keyword:{row[0]}", source_tool="keyword",
                kind="chunk" if row[0].startswith("chunk-") else "entity",
                text=text, score=score, doc_id=row[2], page=row[3], node_ids=row[4],
                edge_ids=row[5], chunk_ids=row[6]))
    return sorted(results, key=lambda item: (-item.score, item.id))[:limit]
