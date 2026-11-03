"""Deduplicated, citation-labelled evidence context with a token budget."""

import hashlib
import re

from kg_agent.models import BuiltContext, ContextItem, Evidence

_EMAIL_ID = re.compile(r"\bemail_id\s*[:=]\s*([^;,\s}]+)", re.IGNORECASE)
_FIELD = re.compile(r"(?:^|;\s*)([A-Za-z][A-Za-z0-9 _-]*)\s*:\s*(.*?)(?=;\s*[A-Za-z][A-Za-z0-9 _-]*\s*:|$)")
_DEMO_PREFIX = re.compile(
    r"^\s*\[?(?:SYNTHETIC|DEMO|FICTIONAL)(?:\s+DEMO)?"
    r"(?:\s+EMAIL|\s+THREAD(?:\s+SUMMARY)?)?\]?\s*[\u2013\u2014:-]\s*",
    re.IGNORECASE)
def _normalize(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" .\t\r\n")


def _source_key(source: Evidence) -> tuple[str, str]:
    match = _EMAIL_ID.search(source.text)
    if match:
        return ("email", match.group(1).casefold())
    if len(source.node_ids) == 1:
        return ("source", source.node_ids[0].casefold())
    normalized = _normalize(source.text)
    return ("text", hashlib.sha256(normalized.encode("utf-8")).hexdigest())


def _fields(text: str) -> dict[str, str]:
    text = _DEMO_PREFIX.sub("", text)
    return {key.strip().casefold().replace(" ", "_"): value.strip()
            for key, value in _FIELD.findall(text)}


def _is_synthetic(text: str) -> bool:
    fields = _fields(text)
    markers = re.sub(r"\b(?:synthetic|demo|fictional)\s*[:=]\s*(?:false|no|0)\b", "",
                     " ".join([text, *fields.values()]).casefold())
    return (any(re.search(rf"\b{word}\b", markers) for word in
                ("synthetic", "demo", "fictional"))
            or "not evidence" in markers
            or any(key in {"synthetic", "demo", "fictional"}
                   and value.casefold() in {"true", "yes", "1"}
                   for key, value in fields.items()))


def _human_text(text: str) -> str:
    """Turn graph property dumps into readable evidence while dropping plumbing."""
    text = _DEMO_PREFIX.sub("", text)
    fields = _fields(text)
    if fields:
        kind = "Email" if any(key in fields for key in ("email_id", "sender", "sender_name")) else "Record"
        pieces = []
        subject = fields.get("subject") or fields.get("title")
        sender = fields.get("sender_name") or fields.get("sender") or fields.get("from")
        recipient = fields.get("recipient_name") or fields.get("recipient") or fields.get("to")
        sent = fields.get("sent_at") or fields.get("date") or fields.get("created_at")
        invoice = fields.get("invoice_number")
        purchase_order = fields.get("po_number") or fields.get("purchase_order")
        body = fields.get("body") or fields.get("summary_text") or fields.get("description")
        if subject:
            pieces.append(f"about {subject.rstrip('. ')}")
        if sender:
            pieces.append(f"from {sender.rstrip('. ')}")
        if recipient:
            pieces.append(f"to {recipient.rstrip('. ')}")
        if sent:
            pieces.append(f"dated {sent.rstrip('. ')}")
        if invoice:
            pieces.append(f"for invoice {invoice.rstrip('. ')}")
        if purchase_order:
            pieces.append(f"for PO {purchase_order.rstrip('. ')}")
        prefix = kind + (" " + ", ".join(pieces) if pieces else "")
        if body:
            cleaned_body = body.strip().rstrip(" .")
            return f"{prefix}: {cleaned_body}" if cleaned_body else prefix
        return prefix

    cleaned = text
    cleaned = re.sub(r"^\s*(?:ConversationSummary\s*:\s*)+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(?:SyntheticEmail|ConversationSummary):[\w./:-]+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bemail_id\s*[:=]\s*[^;,\s}]+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(?:node_ids?|chunk_ids?)\s*[:=]\s*[^;,\s}]+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bbody\s*:\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"(?:\.{3,}|…+)\s*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" ;,.-\t\r\n")


def _source_description(source: Evidence, clean_text: str) -> str:
    fields = _fields(source.text)
    if any(key in fields for key in ("email_id", "sender", "sender_name")):
        parts = ["email"]
        subject = fields.get("subject")
        sender = fields.get("sender_name") or fields.get("sender") or fields.get("from")
        recipient = fields.get("recipient_name") or fields.get("recipient") or fields.get("to")
        sent = fields.get("sent_at") or fields.get("date")
        if subject:
            parts.append(f'about “{subject.rstrip(". ")}”')
        if sender and recipient:
            parts.append(f"from {sender} to {recipient}")
        elif recipient:
            parts.append(f"to {recipient}")
        elif sender:
            parts.append(f"from {sender}")
        if sent:
            parts.append(f"dated {sent}")
        return " ".join(parts)
    if "summary_text" in fields or "ConversationSummary" in source.text:
        return "conversation summary"
    return clean_text[:100] or source.kind.replace("_", " ")


def build_context(evidence: list[Evidence], *, token_budget: int = 6000,
                  relationship_question: bool = False) -> BuiltContext:
    if token_budget <= 0:
        return BuiltContext()
    ordered = sorted(evidence, key=lambda item: (
        0 if relationship_question and item.kind in ("triple", "path") else 1,
        -item.score, item.id))
    items, blocks, metadata, citation_by_key, used = [], [], {}, {}, 0
    synthetic = False
    for source in ordered:
        key = _source_key(source)
        if key in citation_by_key:
            citation_id = citation_by_key[key]
            entry = metadata["sources"][citation_id]
            entry["source_ids"] = list(dict.fromkeys([*entry["source_ids"], source.id]))
            entry["node_ids"] = list(dict.fromkeys([*entry["node_ids"], *source.node_ids]))
            entry["edge_ids"] = list(dict.fromkeys([*entry["edge_ids"], *source.edge_ids]))
            entry["chunk_ids"] = list(dict.fromkeys([*entry["chunk_ids"], *source.chunk_ids]))
            entry["synthetic"] = entry["synthetic"] or _is_synthetic(source.text)
            synthetic = synthetic or entry["synthetic"]
            continue
        clean_text = _human_text(source.text)
        if not clean_text:
            continue
        citation_id = f"N{len(items) + 1}"
        block = f"[{citation_id}] {clean_text}"
        estimate = max(1, (len(block) + 3) // 4)
        if used + estimate > token_budget:
            continue
        safe_evidence = source.model_copy(update={"text": clean_text})
        items.append(ContextItem(citation_id=citation_id, evidence=safe_evidence))
        blocks.append(block)
        citation_by_key[key] = citation_id
        metadata.setdefault("sources", {})[citation_id] = {
            "source_id": source.id,
            "source_ids": [source.id],
            "email_id": _EMAIL_ID.search(source.text).group(1) if _EMAIL_ID.search(source.text) else None,
            "doc_id": source.doc_id,
            "page": source.page,
            "node_ids": list(source.node_ids),
            "edge_ids": list(source.edge_ids),
            "chunk_ids": list(source.chunk_ids),
            "description": _source_description(source, clean_text),
            "synthetic": _is_synthetic(source.text),
        }
        synthetic = synthetic or metadata["sources"][citation_id]["synthetic"]
        used += estimate
    return BuiltContext(items=items, text="\n\n".join(blocks), token_estimate=used,
                        source_metadata={**metadata, "has_synthetic": synthetic})
