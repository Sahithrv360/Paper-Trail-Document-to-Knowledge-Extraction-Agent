"""Deterministic citation and evidence checks with an optional judge hook."""

import re

from kg_agent.models import (AnswerDraft, BuiltContext, VerificationResult)


class AnswerVerifier:
    def __init__(self, judge=None):
        self.judge = judge

    def verify(self, question: str, answer: AnswerDraft, context: BuiltContext,
               sub_questions=None) -> VerificationResult:
        ids = {item.citation_id for item in context.items}
        issues = []
        if not answer.citations:
            issues.append("Answer contains no evidence citations.")
        invalid = sorted(set(answer.citations) - ids)
        if invalid:
            issues.append(f"Unknown citation IDs: {', '.join(invalid)}")
        if not context.items and answer.confidence > 0:
            issues.append("Answer claims confidence without retrieved evidence.")
        unresolved = []
        for sub_question in sub_questions or []:
            if any(item.evidence.id.startswith("aggregate:") for item in context.items):
                continue
            # Normalize punctuation on both sides so names such as
            # "Wells-Carlson." still match the graph's "Wells-Carlson)" form.
            tokens = {token for token in re.findall(r"[\w]+", sub_question.question.casefold())
                      if len(token) > 3}
            evidence_tokens = {token for item in context.items
                               for token in re.findall(r"[\w]+", item.evidence.text.casefold())}
            if tokens and not tokens.intersection(evidence_tokens):
                unresolved.append(sub_question.question)
        if unresolved:
            issues.append("Unanswered sub-questions: " + "; ".join(unresolved))
        supported = not issues and bool(context.items)
        confidence = answer.confidence if supported else min(answer.confidence, 0.25)
        if self.judge and supported:
            try:
                judged = self.judge(question=question, answer=answer, context=context)
                if isinstance(judged, VerificationResult):
                    return judged
            except Exception:
                pass
        return VerificationResult(supported=supported, complete=not unresolved,
                                  issues=issues, confidence=confidence)
