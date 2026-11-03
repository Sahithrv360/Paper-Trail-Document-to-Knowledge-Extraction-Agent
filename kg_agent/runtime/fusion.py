"""Reciprocal Rank Fusion with evidence de-duplication and provenance merging."""

from collections import defaultdict

from kg_agent.models import Evidence


def _dedupe_key(item: Evidence):
    if item.kind == "chunk":
        return ("chunk", item.doc_id, tuple(sorted(item.chunk_ids)) or item.id)
    if item.edge_ids:
        return ("edge", tuple(sorted(item.edge_ids)))
    if item.node_ids:
        return ("node", tuple(sorted(item.node_ids)))
    return (item.kind, item.doc_id, item.id)


def reciprocal_rank_fusion(result_sets: list[list[Evidence]], *, k: int = 60,
                           limit: int = 20) -> list[Evidence]:
    if k <= 0 or limit <= 0:
        return []
    score_by_key = defaultdict(float)
    examples = {}
    tools = defaultdict(set)
    node_ids, edge_ids, chunk_ids = defaultdict(set), defaultdict(set), defaultdict(set)
    for result_set in result_sets:
        for rank, evidence in enumerate(result_set, start=1):
            key = _dedupe_key(evidence)
            score_by_key[key] += 1.0 / (k + rank)
            examples.setdefault(key, evidence)
            tools[key].add(evidence.source_tool)
            node_ids[key].update(evidence.node_ids)
            edge_ids[key].update(evidence.edge_ids)
            chunk_ids[key].update(evidence.chunk_ids)
    ranked = sorted(score_by_key, key=lambda key: (-score_by_key[key], str(key)))[:limit]
    merged = []
    for key in ranked:
        source = examples[key]
        merged.append(source.model_copy(update={
            "score": score_by_key[key],
            "source_tool": "+".join(sorted(tools[key])),
            "node_ids": sorted(node_ids[key]),
            "edge_ids": sorted(edge_ids[key]),
            "chunk_ids": sorted(chunk_ids[key]),
        }))
    return merged
