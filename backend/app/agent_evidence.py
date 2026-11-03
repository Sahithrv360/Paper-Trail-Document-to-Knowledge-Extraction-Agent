"""Human-readable evidence-card assembly for the knowledge graph answer UI."""

import re


_BOILERPLATE = re.compile(
    r"^\s*(?:\[?SYNTHETIC\s+DEMO(?:\s+(?:EMAIL|THREAD(?:\s+SUMMARY)?))?\]?\s*(?:—|â€”|[:-])\s*)+",
    re.IGNORECASE)


def sentence_snippet(value: str, max_sentences: int = 2) -> str:
    """Remove demo headers and return only complete, claim-bearing sentences."""
    text = " ".join(str(value or "").replace("\r", " ").split())
    text = _BOILERPLATE.sub("", text)
    text = re.sub(r"\u2026+\s*$", "", text).strip()
    text = re.sub(r"(?:\.{3,}|…+)\s*$", "", text).strip()
    if not text or text.casefold().startswith("invoice number "):
        return ""
    sentences = re.findall(r"[^.!?]+[.!?](?=\s|$)", text)
    if not sentences:
        return ""
    return " ".join(sentence.strip() for sentence in sentences[:max_sentences]).strip()


def _title(node: dict) -> str:
    kind, props = node.get("type"), node.get("props") or {}
    if kind == "ConversationSummary":
        return "Thread summary"
    if kind == "SyntheticEmail":
        sender = props.get("sender_name") or props.get("sender") or "unknown sender"
        recipient = props.get("recipient_name") or props.get("recipient") or "unknown recipient"
        return f"Email: {sender} to {recipient}"
    if kind == "Invoice":
        number = props.get("invoice_number") or node.get("id")
        status = str(props.get("review_status") or "status unavailable").replace("_", " ")
        return f"Invoice {number} ({status})"
    if kind in {"APReview", "AP Review", "Review", "Approval"}:
        return f"AP review ({str(props.get('review_status') or props.get('status') or 'status unavailable').replace('_', ' ')})"
    return str(props.get("name") or node.get("type") or "Source record")


def build_evidence_cards(evidence: list[dict], graph_nodes: dict[str, dict],
                         graph_edges: list[dict]) -> list[dict]:
    """Convert cited evidence to canonical source cards and nest cited email support."""
    cards = {}
    invoice_by_number = {}
    emails = []
    email_invoice_numbers = {}
    for edge in graph_edges:
        source = graph_nodes.get(edge.get("source"), {})
        target = graph_nodes.get(edge.get("target"), {})
        if source.get("type") == "Invoice" and target.get("type") == "SyntheticEmail":
            number = (source.get("props") or {}).get("invoice_number")
            if number:
                email_invoice_numbers[target.get("id")] = str(number)
        elif target.get("type") == "Invoice" and source.get("type") == "SyntheticEmail":
            number = (target.get("props") or {}).get("invoice_number")
            if number:
                email_invoice_numbers[source.get("id")] = str(number)
    for item in evidence:
        citation = item.get("citation_id")
        if not citation:
            continue
        source_id = item.get("doc_id") if item.get("doc_id") in graph_nodes else None
        if source_id is None:
            candidates = [graph_nodes[node_id] for node_id in item.get("node_ids", [])
                          if node_id in graph_nodes]
            preferred = next((node for node in candidates if node.get("type") in
                              {"SyntheticEmail", "ConversationSummary", "Invoice"}), None)
            source_id = (preferred or (candidates[0] if candidates else {})).get("id")
        if source_id is None:
            continue
        node = graph_nodes[source_id]
        props = node.get("props") or {}
        canonical_id = (props.get("email_id") or props.get("summary_id") or source_id)
        text = props.get("body") or props.get("summary_text") or item.get("text") or ""
        snippet = sentence_snippet(text)
        if not snippet and node.get("type") == "Invoice":
            review = str(props.get("review_status") or "status unavailable").replace("_", " ")
            snippet = f"This invoice is marked {review}."
        card = cards.setdefault(canonical_id, {
            "id": source_id, "canonical_id": canonical_id, "citation_ids": [],
            "source": _title(node), "quote": snippet or sentence_snippet(item.get("text")),
            "type": node.get("type"), "review_status": props.get("review_status"),
            "synthetic": bool(props.get("synthetic") is True or props.get("synthetic_notice")
                               or re.search(r"\b(?:synthetic|demo|fictional|not evidence)\b",
                                            str(text), re.I)),
            "supporting_emails": [],
        })
        if citation not in card["citation_ids"]:
            card["citation_ids"].append(citation)
        if node.get("type") == "Invoice":
            number = str(props.get("invoice_number") or "")
            if number:
                invoice_by_number[number] = card
        elif node.get("type") == "SyntheticEmail":
            emails.append((card, props, source_id))

    for email, props, source_id in emails:
        invoice_number = str(props.get("invoice_number") or
                             email_invoice_numbers.get(source_id) or "")
        parent = invoice_by_number.get(invoice_number)
        if parent and parent is not email:
            child = {key: email[key] for key in
                     ("id", "canonical_id", "citation_ids", "source", "quote", "type", "synthetic")}
            if not any(existing["canonical_id"] == child["canonical_id"]
                       for existing in parent["supporting_emails"]):
                parent["supporting_emails"].append(child)
            cards.pop(email["canonical_id"], None)
    return list(cards.values())
