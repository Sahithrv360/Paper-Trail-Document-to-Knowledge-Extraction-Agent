"""Structured review findings and the record-level validation status.

Every finding names a ``code``, the ``field`` path and/or 1-based ``row`` it concerns,
and a human-readable ``message``. Its effect on the record status is fixed per code in
``CODE_EFFECTS``. Arithmetic here only *detects* discrepancies between printed values;
it never fills an absent value or replaces a printed one.

Status names (kept from the existing store for backward compatibility):

==========================  =================================================
Stored status               Meaning (build-prompt name)
==========================  =================================================
``valid_reconciled``        all checks pass (still ``needs_review``)
``arithmetic_mismatch``     printed values disagree (``valid_mismatch``)
``incomplete_extraction``   required values or rows missing (``incomplete``)
``ambiguous_review``        complete, but something needs a person
``invalid_response``        job-level only: output unparsable; nothing stored
==========================  =================================================
"""

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .matching import text_key

INVOICE_LIKE_TYPES = frozenset({"invoice"})
STATUS_PRECEDENCE = ("incomplete_rows", "mismatch", "incomplete", "ambiguous")
STATUS_FOR_EFFECT = {"incomplete_rows": "incomplete_extraction",
                     "mismatch": "arithmetic_mismatch",
                     "incomplete": "incomplete_extraction",
                     "ambiguous": "ambiguous_review"}

CODE_EFFECTS = {
    # Arithmetic between printed values.
    "arithmetic_line_net": "mismatch",
    "arithmetic_line_tax": "mismatch",
    "arithmetic_line_tax_from_gross": "mismatch",
    "arithmetic_line_gross": "mismatch",
    "arithmetic_sum_net": "mismatch",
    "arithmetic_sum_tax": "mismatch",
    "arithmetic_sum_gross": "mismatch",
    "arithmetic_invoice_total": "mismatch",
    # Completeness.
    "generation_truncated": "incomplete_rows",
    "pages_not_processed": "incomplete_rows",
    "row_count_mismatch": "incomplete_rows",
    "row_count_self_report_mismatch": "incomplete_rows",
    "missing_field": "incomplete",
    "missing_party": "incomplete",
    "no_line_items": "incomplete",
    "item_missing_field": "incomplete",
    # Needs a person even when complete and reconciled.
    "party_address_unseparated": "ambiguous",
    "document_type_unexpected": "ambiguous",
    "repeated_header_row": "ambiguous",
    "carried_forward_row": "ambiguous",
    "page_subtotal_row": "ambiguous",
    "duplicate_row_candidate": "ambiguous",
    "possible_wrapped_description": "ambiguous",
    "evidence_not_in_source": "ambiguous",
    "value_not_in_quote": "ambiguous",
    "evidence_page_invalid": "ambiguous",
    "evidence_geometry_invalid": "ambiguous",
    "unparseable_value": "ambiguous",
    "iban_checksum_invalid": "ambiguous",
    "provider_review_reason": "ambiguous",
    # Informational: recorded for the reviewer, no status effect.
    "row_count_not_independent": "none",
    "legacy_amount_semantics_unknown": "none",
    "evidence_missing": "none",
}
# Codes whose wording already appears in the legacy ``review_reasons`` strings.
LEGACY_REASON_CODES = frozenset({
    "arithmetic_line_net", "arithmetic_line_tax", "arithmetic_line_gross",
    "arithmetic_sum_net", "arithmetic_sum_tax", "arithmetic_sum_gross",
    "arithmetic_invoice_total", "generation_truncated", "missing_field", "missing_party",
    "no_line_items", "item_missing_field", "party_address_unseparated",
    "provider_review_reason", "evidence_missing"})

REQUIRED_INVOICE_FIELDS = ("invoice_number", "invoice_date", "subtotal", "tax_amount",
                           "total_amount")
REQUIRED_ITEM_FIELDS = ("description", "quantity", "unit_price", "net_amount", "gross_amount")
ITEM_NUMERIC_FIELDS = ("quantity", "unit_price", "net_amount", "vat_amount", "gross_amount",
                       "tax_rate_percent")

_CARRIED_FORWARD = re.compile(
    r"\b(balance\s+(b/f|brought\s+forward|c/f|carried\s+forward)|brought\s+forward|"
    r"carried\s+forward|carry\s+forward|b/f|c/f|übertrag|transfer\s+from\s+previous\s+page)\b")
_PAGE_SUBTOTAL = re.compile(r"^(page\s+)?(sub\s*-?\s*total|total|page\s+total|zwischensumme)\b")
_HEADER_WORDS = {"description", "item", "items", "item description", "product", "products",
                 "article", "qty", "quantity", "unit price", "amount", "no.", "#"}
_HEADER_COLUMNS = re.compile(r"\b(qty|quantity|unit\s+price|price|amount|net|vat|gross|tax)\b")


def finding(code: str, message: str, field: Optional[str] = None,
            row: Optional[int] = None, **details) -> dict:
    if code not in CODE_EFFECTS:
        raise ValueError(f"unknown finding code: {code}")
    item = {"code": code, "effect": CODE_EFFECTS[code], "field": field, "row": row,
            "message": message}
    if details:
        item["details"] = details
    return item


def status_from_findings(findings: list) -> str:
    effects = {item["effect"] for item in findings}
    for effect in STATUS_PRECEDENCE:
        if effect in effects:
            return STATUS_FOR_EFFECT[effect]
    return "valid_reconciled"


def _money(value, unit: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(unit, rounding=ROUND_HALF_UP)


def arithmetic_findings(record: dict, precision: int = 2,
                        tolerance: Optional[float] = None) -> list:
    """Compare printed amounts. Messages match the historical warning strings.

    ``precision`` is the number of decimals printed on the invoice; the default
    tolerance is two units in that last place (0.02 for cents), which absorbs
    per-line rounding in sums without hiding a real misread.
    """
    unit = Decimal(1).scaleb(-precision)
    allowed = Decimal(str(tolerance)) if tolerance is not None else unit * 2
    findings = []

    def money(value):
        return _money(value, unit)

    sums = {"net_amount": Decimal("0"), "vat_amount": Decimal("0"),
            "gross_amount": Decimal("0")}
    complete = {key: True for key in sums}
    for index, item in enumerate(record.get("items", []), start=1):
        q, price = item.get("quantity"), item.get("unit_price")
        net, vat, gross = (item.get("net_amount"), item.get("vat_amount"),
                           item.get("gross_amount"))
        rate = item.get("tax_rate_percent")
        path = f"items[{index - 1}]"
        if q is not None and price is not None and net is not None:
            expected = money(Decimal(str(q)) * Decimal(str(price)))
            if abs(expected - money(net)) > allowed:
                findings.append(finding(
                    "arithmetic_line_net",
                    f"Item {index}: net amount {money(net)} differs from quantity × net unit price ({expected}).",
                    field=f"{path}.net_amount", row=index, expected=str(expected),
                    printed=str(money(net))))
        if net is not None and rate is not None and vat is not None:
            expected = money(Decimal(str(net)) * Decimal(str(rate)) / Decimal("100"))
            if abs(expected - money(vat)) > allowed:
                findings.append(finding(
                    "arithmetic_line_tax",
                    f"Item {index}: VAT amount {money(vat)} differs from net amount × VAT rate ({expected}).",
                    field=f"{path}.vat_amount", row=index, expected=str(expected),
                    printed=str(money(vat))))
        if net is not None and rate is not None and vat is None and gross is not None:
            # No printed line tax: compare printed gross − printed net with the rate.
            # The difference is used only for this check and is never stored as VAT.
            difference = money(gross) - money(net)
            expected = money(Decimal(str(net)) * Decimal(str(rate)) / Decimal("100"))
            if abs(expected - difference) > allowed:
                findings.append(finding(
                    "arithmetic_line_tax_from_gross",
                    f"Item {index}: printed gross minus net ({difference}) differs from net amount × VAT rate ({expected}).",
                    field=f"{path}.gross_amount", row=index, expected=str(expected),
                    printed=str(difference)))
        if net is not None and vat is not None and gross is not None:
            expected = money(Decimal(str(net)) + Decimal(str(vat)))
            if abs(expected - money(gross)) > allowed:
                findings.append(finding(
                    "arithmetic_line_gross",
                    f"Item {index}: gross amount {money(gross)} differs from net plus VAT ({expected}).",
                    field=f"{path}.gross_amount", row=index, expected=str(expected),
                    printed=str(money(gross))))
        for key, value in (("net_amount", net), ("vat_amount", vat), ("gross_amount", gross)):
            if value is None:
                complete[key] = False
            else:
                sums[key] += money(value)

    for key, invoice_key, label, code in (
            ("net_amount", "subtotal", "net subtotal", "arithmetic_sum_net"),
            ("vat_amount", "tax_amount", "VAT total", "arithmetic_sum_tax"),
            ("gross_amount", "total_amount", "gross total", "arithmetic_sum_gross")):
        expected = record.get(invoice_key)
        if (expected is not None and record.get("items") and complete[key]
                and abs(sums[key] - money(expected)) > allowed):
            findings.append(finding(
                code,
                f"Line-item {key.replace('_', ' ')} sum {sums[key]} differs from invoice {label} {money(expected)}.",
                field=invoice_key, expected=str(sums[key]), printed=str(money(expected))))

    sub, tax, total = record.get("subtotal"), record.get("tax_amount"), record.get("total_amount")
    if None not in (sub, tax, total) and abs(money(sub) + money(tax) - money(total)) > allowed:
        findings.append(finding(
            "arithmetic_invoice_total",
            f"Invoice net subtotal plus VAT ({money(sub) + money(tax)}) differs from gross total ({money(total)}).",
            field="total_amount", expected=str(money(sub) + money(tax)), printed=str(money(total))))
    return findings


def completeness_findings(record: dict, diagnostics: Optional[dict] = None) -> list:
    findings = []
    for name in REQUIRED_INVOICE_FIELDS:
        if record.get(name) is None:
            findings.append(finding("missing_field", f"Invoice field {name} was not extracted.",
                                    field=name))
    for role in ("vendor", "customer"):
        party = record.get(role) or {}
        if not party.get("name"):
            findings.append(finding("missing_party", f"{role.title()} name was not identified.",
                                    field=f"{role}.name"))
        elif not party.get("address"):
            findings.append(finding("party_address_unseparated",
                                    f"{role.title()} address was not independently identified.",
                                    field=f"{role}.address"))
    if diagnostics and diagnostics.get("generation_may_be_truncated"):
        findings.append(finding("generation_truncated",
                                "Model output reached its token limit; the line-item table may be incomplete."))
    if diagnostics and diagnostics.get("pages_ignored"):
        findings.append(finding("pages_not_processed",
                                f"The provider reads one page; {diagnostics['pages_ignored']} later "
                                "page(s) were not processed, so rows may be missing."))
    items = record.get("items") or []
    if not items:
        findings.append(finding("no_line_items", "No line items were extracted.", field="items"))
    for index, item in enumerate(items, start=1):
        for name in REQUIRED_ITEM_FIELDS:
            if item.get(name) is None:
                findings.append(finding("item_missing_field",
                                        f"Item {index} field {name} was not extracted.",
                                        field=f"items[{index - 1}].{name}", row=index))
    return findings


def row_findings(record: dict, parse=None) -> list:
    """Row-count cross-checks plus multi-page artefacts. Nothing is dropped or merged."""
    findings = []
    items = record.get("items") or []
    independent = getattr(parse, "table_row_count", None) if parse is not None else None
    if isinstance(independent, int) and not isinstance(independent, bool):
        if independent != len(items):
            findings.append(finding(
                "row_count_mismatch",
                f"Parser table structure shows {independent} line-item rows; {len(items)} were extracted.",
                field="items", parser_rows=independent, extracted_rows=len(items),
                method=getattr(parse, "table_row_method", None)))
    seen = record.get("row_count_seen")
    if isinstance(seen, int) and not isinstance(seen, bool):
        if seen != len(items):
            findings.append(finding(
                "row_count_self_report_mismatch",
                f"The model reported seeing {seen} rows but returned {len(items)}.",
                field="items", reported_rows=seen, extracted_rows=len(items)))
        elif not isinstance(independent, int):
            findings.append(finding(
                "row_count_not_independent",
                "Row count agrees only with the model's own count, which is not independent evidence.",
                field="items"))

    signatures = {}
    for index, item in enumerate(items, start=1):
        description = text_key(item.get("description")) or ""
        path = f"items[{index - 1}]"
        numbers = [item.get(name) for name in ITEM_NUMERIC_FIELDS]
        if description in _HEADER_WORDS or (
                ("description" in description or description.startswith("item"))
                and _HEADER_COLUMNS.search(description) and all(v is None for v in numbers)):
            findings.append(finding("repeated_header_row",
                                    f"Item {index} looks like a repeated table header.",
                                    field=f"{path}.description", row=index))
        if _CARRIED_FORWARD.search(description):
            findings.append(finding("carried_forward_row",
                                    f"Item {index} looks like a carried-forward/brought-forward line, not a product row.",
                                    field=f"{path}.description", row=index))
        elif _PAGE_SUBTOTAL.search(description):
            findings.append(finding("page_subtotal_row",
                                    f"Item {index} looks like a page subtotal or total line.",
                                    field=f"{path}.description", row=index))
        if index > 1 and description and all(v is None for v in numbers):
            findings.append(finding("possible_wrapped_description",
                                    f"Item {index} has text but no amounts; it may be a wrapped "
                                    f"description of item {index - 1}.",
                                    field=f"{path}.description", row=index))
        signature = (description, *numbers)
        if description and any(v is not None for v in numbers):
            if signature in signatures:
                findings.append(finding(
                    "duplicate_row_candidate",
                    f"Item {index} repeats item {signatures[signature]} exactly; check for a row duplicated across pages.",
                    field=path, row=index, duplicate_of_row=signatures[signature]))
            else:
                signatures[signature] = index
    return findings


def document_type_findings(record: dict) -> list:
    doc_type = record.get("doc_type")
    if doc_type is None or doc_type in INVOICE_LIKE_TYPES:
        return []
    return [finding("document_type_unexpected",
                    f"Document was classified as {doc_type!r}, not an invoice; it is retained "
                    "for review and only a Document node is written to the graph.",
                    field="doc_type")]


def iban_is_valid(value: str) -> bool:
    """ISO 13616 structure and mod-97 check. Used to flag, never to repair."""
    compact = re.sub(r"\s+", "", value or "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1


def identifier_findings(record: dict) -> list:
    iban = record.get("bank_account_iban")
    if iban and not iban_is_valid(iban):
        return [finding("iban_checksum_invalid",
                        f"IBAN {iban!r} fails the ISO 13616 structure/checksum test; it may be "
                        "misread. It is stored exactly as read.", field="bank_account_iban")]
    return []


def legacy_amount_findings(record: dict) -> list:
    findings = []
    for index, item in enumerate(record.get("items") or [], start=1):
        if (item.get("amount") is not None and item.get("amount_semantics") in (None, "unknown")
                and item.get("net_amount") is None and item.get("gross_amount") is None):
            findings.append(finding(
                "legacy_amount_semantics_unknown",
                f"Item {index} has only the deprecated `amount`, whose net/gross meaning is unknown.",
                field=f"items[{index - 1}].amount", row=index))
    return findings


def structured_findings(record: dict, diagnostics: Optional[dict] = None, parse=None,
                        precision: int = 2) -> list:
    return (arithmetic_findings(record, precision) + completeness_findings(record, diagnostics)
            + row_findings(record, parse) + document_type_findings(record)
            + identifier_findings(record) + legacy_amount_findings(record))
