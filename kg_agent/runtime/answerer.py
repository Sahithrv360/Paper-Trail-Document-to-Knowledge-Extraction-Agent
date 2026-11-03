"""Generate concise answers whose citations must reference supplied evidence IDs."""

import json

from kg_agent.llm import repair_json
from kg_agent.models import AnswerDraft, BuiltContext


class AnswerGenerator:
    def __init__(self, llm=None, system_prompt: str = ""):
        self.llm, self.system_prompt = llm, system_prompt

    def answer(self, question: str, context: BuiltContext) -> AnswerDraft:
        valid_ids = {item.citation_id for item in context.items}
        if not context.items:
            return AnswerDraft(answer="I could not find supporting evidence in the knowledge graph.",
                               confidence=0.0, missing_info="No matching evidence was retrieved.")
        if self.llm is None:
            triples = [item for item in context.items if item.evidence.kind in ("triple", "path")]
            if triples:
                selected = triples[:4]
                citations = [item.citation_id for item in selected]
                relations = "; ".join(f"{item.evidence.text} [{item.citation_id}]"
                                       for item in selected)
                return AnswerDraft(answer=f"The graph supports these connections: {relations}",
                    citations=citations, confidence=0.6,
                    reasoning_summary="Combined the highest ranked graph triples.")
            first = context.items[0]
            return AnswerDraft(answer=f"The available evidence says: {first.evidence.text}",
                               citations=[first.citation_id], confidence=0.45,
                               reasoning_summary="Deterministic evidence excerpt.")
        messages = [
            {"role": "system", "content": self.system_prompt or
             "Answer only from supplied evidence. Cite every factual claim with evidence IDs. "
             "If evidence is insufficient, say what is missing. Return JSON with answer, "
             "citations, reasoning_summary, confidence, missing_info."},
            {"role": "user", "content": f"Question: {question}\nEvidence:\n{context.text}\n"
             "Return JSON: {\"answer\":\"...\",\"citations\":[],"
             "\"reasoning_summary\":\"...\",\"confidence\":0.0,\"missing_info\":\"...\"}"},
        ]
        try:
            raw = self.llm.complete(messages, json_mode=True, response_model=AnswerDraft)
            result = AnswerDraft.model_validate_json(repair_json(raw))
        except Exception:
            first = context.items[0]
            return AnswerDraft(answer=f"The available evidence says: {first.evidence.text}",
                citations=[first.citation_id], confidence=0.35,
                reasoning_summary="Used the deterministic answer because the model was unavailable.",
                missing_info="Model-generated phrasing was unavailable.")
        result.citations = [citation for citation in result.citations if citation in valid_ids]
        if not result.citations:
            result.answer = "I could not produce an answer supported by the retrieved evidence."
            result.confidence = min(result.confidence, 0.2)
            result.missing_info = result.missing_info or "The answer did not cite supplied evidence."
        return result
