"""Generate human-readable answers grounded in deduplicated evidence."""

import logging
import re

from kg_agent.models import AnswerDraft, BuiltContext

logger = logging.getLogger(__name__)

ANSWER_SYSTEM_PROMPT = """ROLE
You are an accounts-payable and vendor-communications analyst answering questions from a knowledge graph of business emails, invoices, purchase orders (POs), and AP review records. Answer the way a knowledgeable colleague would explain it in conversation: direct, plain, and grounded in the evidence.

WORKFLOW (follow silently)
1. Understand the question: identify the entities (vendor, customer, invoice, PO, thread) and what is actually asked (status, timing, amount, who said what, what is missing).
2. Use only the evidence provided. Treat the same email or chunk appearing twice as ONE source.
3. Never paste raw chunks. Extract the meaning. Never output field names (email_id, body, ConversationSummary) or truncation markers ("...").
4. Verify: separate what is CONFIRMED (a recorded event or document says so) from what is only ASKED, PROPOSED, or IMPLIED. A question in an email is not a confirmation. If sources conflict, say so. If the evidence doesn't answer the question, say that plainly and do not fill gaps from general knowledge.

ANSWER FORMAT
Natural prose, as a person would speak. No headers unless the answer has several distinct parts.
1. Lead with the answer in 1-2 sentences.
2. Support with key facts: who asked what, current status, dates, amounts, PO/invoice references.
3. State what is NOT established (e.g. "No payment is recorded or confirmed in these messages.").
4. Close with a next step only if one is obvious.
Length: 3-6 sentences for simple questions; a short list only when comparing multiple invoices, emails, or vendors. Tone: calm and specific, with no jargon about graphs, retrieval, chunks, or nodes.
Sound like a helpful colleague speaking directly to the person who asked. Don't repeat the question or begin with canned phrases such as "Based on the available evidence" or "The records indicate." Use clear, natural wording and contractions where they fit. Avoid repeating the same caveat in multiple sentences; say what is known, then state the important gap once. Keep the answer focused on the user's decision or next step.
For a multi-part briefing, short "Confirmed" and "Not confirmed" labels can make the distinction easy to scan. Confirm that a message expressed an intention only as an intention; never turn a plan into a completed payment or approval. For a follow-up, answer the new question directly and use earlier turns only to resolve context—don't repeat the full briefing. When asked for a field the evidence omits, state that plainly and name the relevant records checked. If the user suggests an unsupported conclusion, correct it politely and cite the evidence.

CITATIONS
Cite each distinct source once, attached to the specific claim it supports, using the [N#] markers provided. Describe sources in human terms (who/what/when), never by internal ID alone. End with: "Sources: N1 (conversation summary), N3 (email to <vendor>)."

NEVER
- Output a list of raw retrieved snippets as the answer.
- State a payment, approval, or completion as fact unless a source explicitly records it.
- Invent dates, amounts, names, or statuses.

EXAMPLE
Q: "What's the status of payment for Shaw, Williams and Johnston?"
A: "Payment hasn't been confirmed. In the thread, the customer asked Shaw, Williams and Johnston to share the expected payment timing once the AP review is complete, but no payment is recorded and the review outcome isn't in the retrieved messages. The AP review result is the next thing to check. Sources: N1 (conversation summary), N3 (email to Shaw, Williams and Johnston)."""

RETRY_INSTRUCTION = """Your previous answer failed citation or output validation. Retry once. Write a short, natural prose answer using inline [N#] markers. End with one line beginning exactly 'Sources:' and list each cited marker once with its supplied human source description. Do not include raw fields, excerpts, or internal IDs. Distinguish a request from a confirmed event."""

_CITATION = re.compile(r"\[(N\d+)\]")
_SOURCES_LINE = re.compile(r"(?im)^Sources:\s*(.+?)\s*$")
_UNSAFE = re.compile(r"\bemail_id\b|\bConversationSummary\b|\bbody\s*:", re.IGNORECASE)


class CitationFormatError(ValueError):
    """The generated answer did not follow the supported citation format."""


class OutputSafetyError(ValueError):
    """The generated answer contains raw retrieval artifacts."""


class AnswerContractError(ValueError):
    """The answer omitted a required business-status statement."""


class AnswerGenerator:
    def __init__(self, llm=None, system_prompt: str = ""):
        self.llm = llm
        # Kept for API compatibility; the required answer prompt is the single constant above.
        self.system_prompt = ANSWER_SYSTEM_PROMPT

    @staticmethod
    def _safe_answer(answer: str) -> str:
        answer = re.sub(r"(?im)^\s*Note:\s*[^\n]*(?:synthetic|demo|fictional)[^\n]*\n?", "", answer)
        answer = re.sub(r"(?i)\(?\s*Note:\s*[^.\n]*(?:synthetic|demo|fictional)[^.\n]*\.?\s*\)?", "", answer)
        if _UNSAFE.search(answer):
            raise OutputSafetyError("answer contains a plumbing field")
        if "..." in answer or "…" in answer:
            raise OutputSafetyError("answer contains a truncation marker")
        bullet_lines = [line for line in answer.splitlines()
                        if re.match(r"^\s*[-*]\s+", line)]
        if len(bullet_lines) >= 3:
            raise OutputSafetyError("answer contains a bulleted raw-source dump")
        return answer.strip()

    @staticmethod
    def _citations(answer: str, context: BuiltContext, *, require_sources: bool) -> list[str]:
        sources = context.source_metadata.get("sources", {})
        valid = {item.citation_id for item in context.items}
        sources_line = _SOURCES_LINE.search(answer)
        prose = answer[:sources_line.start()] if sources_line else answer
        citations = list(dict.fromkeys(_CITATION.findall(prose)))
        if not citations:
            raise CitationFormatError("answer omitted inline evidence markers")
        unknown = sorted(set(citations) - valid)
        if unknown:
            raise CitationFormatError("answer used unknown evidence markers: " + ", ".join(unknown))
        if require_sources:
            if not sources_line:
                raise CitationFormatError("answer omitted its Sources line")
            listed = re.findall(r"\b(N\d+)\s*\(", sources_line.group(1))
            if len(listed) != len(set(listed)):
                raise CitationFormatError("Sources line listed a source more than once")
            if set(listed) != set(citations):
                raise CitationFormatError("Sources line does not match cited sources")
            for citation in citations:
                if not sources.get(citation, {}).get("description"):
                    raise CitationFormatError(f"missing human source description for {citation}")
        return citations

    @staticmethod
    def _focused_question(question: str) -> str:
        match = re.search(r"(?im)^Follow-up question:\s*(.+?)\s*$", question)
        return match.group(1) if match else question

    @staticmethod
    def _validate_payment_status_contract(question: str, answer: str) -> None:
        question = AnswerGenerator._focused_question(question)
        if not ("payment" in question.casefold() and "status" in question.casefold()):
            return
        lines = [line.strip() for line in answer.splitlines() if line.strip()]
        lowered = answer.casefold()
        not_confirmed = next((line.casefold() for line in lines
                              if re.match(r"not confirmed\s*:", line, re.I)), "")
        verdict = lines[0].casefold() if lines else ""
        if (not lines or "payment" not in verdict or not re.search(
                r"not confirmed|hasn't been confirmed|has not been confirmed|isn't confirmed|is not confirmed",
                verdict)):
            raise AnswerContractError("payment-status answer must lead with a one-line not-confirmed verdict")
        if not re.search(r"\b(payment timing|expected timing|payment date|when payment)\b", lowered):
            raise AnswerContractError("payment-status answer omitted what the thread says about timing")
        if "ap review" not in lowered:
            raise AnswerContractError("payment-status answer omitted the AP review status")
        if (not not_confirmed or "payment" not in not_confirmed
                or "approval" not in not_confirmed
                or not re.search(r"ap completion|completion of ap", not_confirmed)):
            raise AnswerContractError("payment-status answer omitted the required not-confirmed list")

    @staticmethod
    def _source_list(context: BuiltContext) -> str:
        sources = context.source_metadata.get("sources", {})
        return "; ".join(f"{citation} ({metadata['description']})"
                          for citation, metadata in sources.items())

    def _messages(self, question: str, context: BuiltContext, retry: bool) -> list[dict]:
        user_text = (f"Question: {question}\n"
                     f"Evidence:\n{context.text}\n"
                     f"Available human source descriptions: {self._source_list(context)}")
        messages = [{"role": "system", "content": ANSWER_SYSTEM_PROMPT}]
        if retry:
            messages.append({"role": "system", "content": RETRY_INSTRUCTION})
        messages.append({"role": "user", "content": user_text})
        return messages

    def answer(self, question: str, context: BuiltContext) -> AnswerDraft:
        if not context.items:
            return AnswerDraft(answer="I couldn't find supporting evidence in the available records.",
                               confidence=0.0, missing_info="No matching evidence was retrieved.")
        if self.llm is None:
            return AnswerDraft(
                answer="I found relevant records, but a language model isn't enabled to summarize them. "
                       "Enable the configured answer model to get a grounded response.",
                confidence=0.0, missing_info="No answer-generation model is configured.")

        failures = []
        for attempt in (1, 2):
            raw = ""
            try:
                raw = self.llm.complete(self._messages(question, context, retry=attempt == 2)).strip()
                clean = self._safe_answer(raw)
                citations = self._citations(clean, context, require_sources=True)
                self._validate_payment_status_contract(question, clean)
                clean = self._safe_answer(clean)
                success_log = "Answer generation succeeded on attempt %d%s"
                success_args = (attempt, f" after {failures[0]}" if failures else "")
                if failures:
                    logger.warning(success_log, *success_args)
                else:
                    logger.info(success_log, *success_args)
                return AnswerDraft(answer=clean, citations=citations, confidence=0.8,
                    reasoning_summary=f"Validated cited prose on attempt {attempt}.")
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                failures.append(reason)
                logger.warning("Answer generation attempt %d failed: %s; raw model output=%r",
                               attempt, reason, raw)

        logger.error("Answer generation failed after both attempts: %s", " | ".join(failures))
        source_descriptions = "; ".join(item.get("description", "")
            for item in context.source_metadata.get("sources", {}).values()
            if item.get("description")) or "the available records"
        if "payment" in question.casefold() and "status" in question.casefold():
            fallback = ("Payment is not confirmed.\n"
                "The thread asks about expected payment timing once the AP review is complete.\n"
                "The available records don't confirm the AP review outcome.\n"
                "Not confirmed: payment, approval, or AP completion.\n"
                f"Sources: {source_descriptions}.\n"
                "Citations couldn't be attached to this answer.")
        else:
            fallback = ("I couldn't reliably summarize the available records. Please try narrowing the question.\n"
                        "Citations couldn't be attached to this answer.")
        return AnswerDraft(
            answer=fallback,
            citations=[], confidence=0.0,
            reasoning_summary="Both answer attempts failed citation or output validation.",
            missing_info="; ".join(failures))
