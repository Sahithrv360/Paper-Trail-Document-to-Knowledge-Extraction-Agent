"""Entity linking by exact name/alias, fuzzy similarity, then optional embeddings."""

from difflib import SequenceMatcher
import math

from kg_agent.models import EntityCandidate, LinkedEntity


def _cosine(left, right):
    denominator = math.sqrt(sum(x*x for x in left) * sum(y*y for y in right))
    return sum(x*y for x, y in zip(left, right)) / denominator if denominator else 0.0


def link_entity(store, mention: str, *, embedder=None, threshold: float = 0.6,
                top_k: int = 3) -> LinkedEntity:
    normalized = " ".join(mention.casefold().split())
    nodes = list(store.all_nodes())
    exact = []
    for node in nodes:
        names = [node.name, *node.aliases, node.id]
        if any(" ".join(name.casefold().split()) == normalized for name in names):
            exact.append(EntityCandidate(node_id=node.id, name=node.name, score=1.0))
    if exact:
        return LinkedEntity(mention=mention, node_id=exact[0].node_id,
                            confidence=1.0, candidates=exact[:top_k])

    try:
        from rapidfuzz.fuzz import ratio
        similarity = lambda a, b: ratio(a, b) / 100
    except ImportError:
        similarity = lambda a, b: SequenceMatcher(None, a, b).ratio()
    fuzzy = []
    for node in nodes:
        score = max([similarity(normalized, " ".join(value.casefold().split()))
                     for value in [node.name, *node.aliases, node.id]] or [0.0])
        fuzzy.append((score, node))
    fuzzy.sort(key=lambda row: (-row[0], row[1].id))
    best_fuzzy, node_fuzzy = fuzzy[0] if fuzzy else (0.0, None)
    candidates = [EntityCandidate(node_id=node.id, name=node.name, score=score)
                  for score, node in fuzzy[:top_k]]
    if best_fuzzy >= 0.85:
        return LinkedEntity(mention=mention, node_id=node_fuzzy.id,
                            confidence=best_fuzzy, candidates=candidates)

    if embedder and nodes:
        texts = [f"{node.name}. {node.description}" for node in nodes]
        vectors = embedder.embed([mention, *texts])
        candidates = [EntityCandidate(node_id=node.id, name=node.name,
                                      score=_cosine(vectors[0], vector))
                      for node, vector in zip(nodes, vectors[1:])]
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.node_id))
        candidates = candidates[:top_k]
        if candidates and candidates[0].score >= threshold:
            return LinkedEntity(mention=mention, node_id=candidates[0].node_id,
                                confidence=candidates[0].score, candidates=candidates)

    return LinkedEntity(mention=mention, node_id=None,
                        confidence=candidates[0].score if candidates else 0.0,
                        candidates=candidates)
