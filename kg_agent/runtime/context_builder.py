"""Citation-labelled evidence context with a configurable token budget."""

from kg_agent.models import BuiltContext, ContextItem, Evidence


def build_context(evidence: list[Evidence], *, token_budget: int = 6000,
                  relationship_question: bool = False) -> BuiltContext:
    if token_budget <= 0:
        return BuiltContext()
    ordered = sorted(evidence, key=lambda item: (
        0 if relationship_question and item.kind in ("triple", "path") else 1,
        -item.score, item.id))
    counters = {"chunk": 0, "triple": 0, "path": 0, "community_summary": 0, "entity": 0}
    labels = {"chunk": "C", "triple": "T", "path": "P",
              "community_summary": "S", "entity": "N"}
    items, blocks, used = [], [], 0
    for source in ordered:
        kind = source.kind if source.kind in counters else "entity"
        counters[kind] += 1
        citation_id = f"{labels[kind]}{counters[kind]}"
        block = f"[{citation_id}] {source.text}"
        estimate = max(1, (len(block) + 3) // 4)
        if used + estimate > token_budget:
            continue
        items.append(ContextItem(citation_id=citation_id, evidence=source))
        blocks.append(block)
        used += estimate
    return BuiltContext(items=items, text="\n\n".join(blocks), token_estimate=used)
