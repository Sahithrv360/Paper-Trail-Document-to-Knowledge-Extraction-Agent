"""Optional cross-encoder or LLM reranking, with a deterministic lexical fallback."""

from pydantic import BaseModel, Field
import re

from kg_agent.models import Evidence


class RelevanceScore(BaseModel):
    scores: list[float] = Field(min_length=1)


class EvidenceReranker:
    def __init__(self, *, cross_encoder=None, llm=None):
        self.cross_encoder, self.llm = cross_encoder, llm

    def rerank(self, question: str, evidence: list[Evidence], *, limit: int = 8):
        if not evidence or limit <= 0:
            return []
        if self.cross_encoder is not None:
            pairs = [(question, item.text) for item in evidence]
            scores = self.cross_encoder.predict(pairs)
        elif self.llm is not None:
            try:
                response = self.llm.complete([
                    {"role": "system", "content": "Score each evidence item's relevance to "
                     "the question from 0 to 1. Return JSON {\"scores\":[...]}."},
                    {"role": "user", "content": f"Question: {question}\nEvidence:\n" +
                     "\n".join(f"{i}: {item.text}" for i, item in enumerate(evidence))},
                ], json_mode=True, response_model=RelevanceScore)
                scores = RelevanceScore.model_validate_json(response).scores
                if len(scores) != len(evidence):
                    raise ValueError("Reranker returned the wrong score count")
            except Exception:
                scores = self._lexical_scores(question, evidence)
        else:
            scores = self._lexical_scores(question, evidence)
        ranked = sorted(zip(scores, evidence), key=lambda pair: (-float(pair[0]), pair[1].id))
        return [item.model_copy(update={"score": float(score)})
                for score, item in ranked[:limit]]

    @staticmethod
    def _lexical_scores(question, evidence):
        terms = set(re.findall(r"[a-z0-9]+", question.casefold()))
        terms -= {"the", "and", "for", "which", "what", "who", "how", "is", "are",
                  "was", "were", "from", "with", "does", "did", "to", "of", "in"}
        return [len(terms & set(re.findall(r"[a-z0-9]+", item.text.casefold()))) /
                max(1, len(terms))
                for item in evidence]
