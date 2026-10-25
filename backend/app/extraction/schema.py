"""Canonical invoice fields and safe normalization for Donut annotations."""

import re
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


# Digits with grouping/decimal marks; spaces, apostrophes and narrow spaces group digits.
_NUMBER_CORE = re.compile(r"\d[\d.,\s'\u00a0\u202f]*")


def parse_amount(value: Any) -> Optional[Decimal]:
    """Parse an unambiguous numeric amount; return None instead of guessing."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            result = Decimal(str(value))
            return result if result.is_finite() and result >= 0 else None
        except InvalidOperation:
            return None
    text = str(value).strip()
    if not text:
        return None
    # Currency labels may surround the number ("$ 7.00", "7,00 EUR") and are ignored,
    # never inferred. Anything else inside the digit run (OCR noise such as "69A,9O")
    # makes the value unreadable instead of silently dropping characters.
    digits = [index for index, char in enumerate(text) if char.isdigit()]
    if not digits:
        return None
    prefix, core, suffix = text[:digits[0]], text[digits[0]:digits[-1] + 1], text[digits[-1] + 1:]
    if _NUMBER_CORE.fullmatch(core) is None:
        return None
    if prefix.rstrip().endswith(("-", "(")) or suffix.lstrip().startswith(")"):
        return None  # Negative invoice amounts are not accepted as positive values.
    text = re.sub(r"[\s'  ]", "", core)
    if text.count(",") and text.count("."):
        decimal_mark = "." if text.rfind(".") > text.rfind(",") else ","
        grouping_mark = "," if decimal_mark == "." else "."
        text = text.replace(grouping_mark, "").replace(decimal_mark, ".")
    elif text.count(",") == 1:
        left, right = text.split(",")
        text = left + ("." + right if len(right) != 3 else right)
    elif text.count(",") > 1:
        text = text.replace(",", "")
    elif text.count(".") > 1:
        parts = text.split(".")
        text = "".join(parts[:-1]) + "." + parts[-1]
    try:
        result = Decimal(text)
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def parse_percent(value: Any) -> Optional[Decimal]:
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    return parse_amount(value)


def _as_object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), None)


def unpack_ground_truth(value: Any) -> dict:
    """Decode the dataset's JSON string and unwrap its gt_parse object."""
    import json

    parsed = json.loads(value) if isinstance(value, str) else value
    parsed = _as_object(parsed)
    parsed = _as_object(parsed.get("gt_parse", parsed))
    # A small number of rows use a "None" key instead of header/items/summary.
    if "None" in parsed and len(parsed) == 1:
        parsed = _as_object(parsed["None"])
    return parsed


def _sections(parsed: dict):
    header = _as_object(parsed.get("header"))
    summary = _as_object(parsed.get("summary"))
    if not header:
        header = parsed
    if not summary:
        summary = parsed
    items = parsed.get("items", [])
    return header, summary, items if isinstance(items, list) else []


@dataclass
class InvoiceRecord:
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    customer: dict = field(default_factory=lambda: {
        "name": None, "address": None, "phone": None, "email": None, "tax_id": None
    })
    vendor: dict = field(default_factory=lambda: {
        "name": None, "address": None, "tax_id": None
    })
    items: list = field(default_factory=list)
    total_amount: Optional[float] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    currency: Optional[str] = None
    bank_account_iban: Optional[str] = None
    provenance: dict = field(default_factory=dict)
    evidence: Optional[dict] = None
    confidence: Optional[float] = None
    review_status: str = "needs_review"
    validation_status: str = "incomplete_extraction"
    review_reasons: list = field(default_factory=list)
    # Milestone 1 data contract (all optional; absent in legacy stored records).
    doc_type: Optional[str] = None            # provider classification; None = not classified
    row_count_seen: Optional[int] = None      # model self-report; not independent evidence
    extensions: dict = field(default_factory=dict)       # jurisdiction-specific raw extras
    findings: list = field(default_factory=list)         # structured review findings
    field_evidence: dict = field(default_factory=dict)   # path -> raw_text/value/evidence
    validation_details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_donut_result(value: Any, provenance: Optional[dict] = None) -> InvoiceRecord:
    """Map Donut's nested labels to app fields without inventing unavailable data."""
    parsed = unpack_ground_truth(value)
    header, summary, raw_items = _sections(parsed)
    total = _first_present(summary.get("total_gross_worth"), summary.get("invoice_amount"))
    amount = parse_amount(total)
    normalized_items = []
    for row_number, item in enumerate(raw_items, start=1):
        item = _as_object(item)
        description = item.get("item_desc") or item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Donut item row {row_number} has no readable description")
        net_value = _first_present(item.get("item_net_worth"), item.get("total_net_worth"),
                                   item.get("net_amount"))
        gross_value = _first_present(item.get("item_gross_worth"), item.get("gross_amount"))
        vat_value = _first_present(item.get("item_vat_amount"), item.get("vat_amount"))
        net = parse_amount(net_value)
        gross = parse_amount(gross_value)
        vat = parse_amount(vat_value)
        # Arithmetic may detect a mismatch but never fills an unprinted amount. The
        # dataset has no line-VAT label, so gross - net is kept apart as a check value.
        derived = {}
        if vat is None and net is not None and gross is not None:
            derived["vat_amount"] = {"value": _decimal_to_float(gross - net),
                                     "method": "printed_gross_minus_printed_net",
                                     "use": "validation_only"}
        legacy_value = _first_present(gross_value, net_value, item.get("amount"))
        legacy_semantics = ("gross" if gross_value is not None else
                            "net" if net_value is not None else
                            "unknown" if item.get("amount") is not None else None)
        normalized_items.append({
            "source_row": row_number,
            "description": description.strip(),
            "quantity": _decimal_to_float(parse_amount(
                _first_present(item.get("item_qty"), item.get("quantity")))),
            "unit_price": _decimal_to_float(parse_amount(
                _first_present(item.get("item_net_price"), item.get("unit_price")))),
            "net_amount": _decimal_to_float(net),
            "vat_amount": _decimal_to_float(vat),
            "gross_amount": _decimal_to_float(gross),
            # Deprecated compatibility field: retain its historical source choice
            # and state its semantics rather than silently reinterpreting it.
            "amount": _decimal_to_float(parse_amount(legacy_value)),
            "amount_semantics": legacy_semantics,
            "field_origins": {"vat_amount": "model_output" if vat is not None else "not_printed"},
            "derived": derived,
            "tax_rate_percent": _decimal_to_float(parse_percent(item.get("item_vat"))),
            "page": None,
        })
    record = InvoiceRecord(
        invoice_number=_text(header.get("invoice_no") or header.get("invoice_number")),
        invoice_date=_text(header.get("invoice_date")),
        # Dataset labels combine party name and address. Preserve the source text as
        # the name field; do not fabricate a split or contact details.
        customer={**_split_party(header.get("client")),
                  "phone": None, "email": None,
                  "tax_id": _text(header.get("client_tax_id"))},
        vendor={**_split_party(header.get("seller")),
                "tax_id": _text(header.get("seller_tax_id"))},
        items=normalized_items,
        total_amount=_decimal_to_float(amount),
        subtotal=_decimal_to_float(parse_amount(summary.get("total_net_worth"))),
        tax_amount=_decimal_to_float(parse_amount(summary.get("total_vat"))),
        currency=None,
        bank_account_iban=_text(header.get("iban")),
        provenance=provenance or {},
    )
    validate_invoice(record.to_dict())
    return record


def _split_party(value: Any) -> dict:
    """Separate an explicitly multiline party label; never guess from commas."""
    text = _text(value)
    if not text:
        return {"name": None, "address": None}
    lines = [line.strip() for line in re.split(r"\r?\n", text) if line.strip()]
    if len(lines) > 1:
        return {"name": lines[0], "address": "\n".join(lines[1:])}
    return {"name": text, "address": None}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    return None if text.casefold() in {"", "none", "null", "n/a", "na"} else text


def validate_invoice(value: Any) -> None:
    """Raise ValueError if a normalized extraction violates the output contract."""
    if not isinstance(value, dict):
        raise ValueError("invoice output must be an object")
    for key in ("invoice_number", "invoice_date", "currency", "doc_type"):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ValueError(f"{key} must be a string or null")
    seen = value.get("row_count_seen")
    if seen is not None and (isinstance(seen, bool) or not isinstance(seen, int) or seen < 0):
        raise ValueError("row_count_seen must be a non-negative integer or null")
    for party_name, fields in (("customer", ("name", "address", "phone", "email", "tax_id")),
                               ("vendor", ("name", "address", "tax_id"))):
        party = value.get(party_name)
        if not isinstance(party, dict) or any(
            party.get(key) is not None and not isinstance(party[key], str) for key in fields
        ):
            raise ValueError(f"{party_name} must contain string-or-null fields")
    for field_name in ("total_amount", "subtotal", "tax_amount"):
        total = value.get(field_name)
        if total is not None and (not isinstance(total, (int, float)) or
                                  not math.isfinite(total) or total < 0):
            raise ValueError(f"{field_name} must be a non-negative number or null")
    if not isinstance(value.get("items"), list):
        raise ValueError("items must be a list")
    for item in value["items"]:
        if not isinstance(item, dict) or not (item.get("description") is None or
                                              isinstance(item.get("description"), str)):
            raise ValueError("each item must be an object with a string-or-null description")
        if "source_row" in item and (isinstance(item["source_row"], bool) or
                                      not isinstance(item["source_row"], int) or
                                      item["source_row"] < 1):
            raise ValueError("item source_row must be a positive integer")
        page = item.get("page")
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise ValueError("item page must be a positive integer or null")
        for key in ("quantity", "unit_price", "net_amount", "vat_amount", "gross_amount",
                    "amount", "tax_rate_percent"):
            numeric = item.get(key)
            if numeric is not None and (not isinstance(numeric, (int, float)) or
                                        not math.isfinite(numeric) or numeric < 0):
                raise ValueError(f"item {key} must be a non-negative number or null")


def _decimal_to_float(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None


def record_from_canonical(value: Any, provenance: Optional[dict] = None) -> InvoiceRecord:
    """Build a validated InvoiceRecord from model output already in canonical shape.

    Unknown or malformed values become null rather than being guessed; structural
    violations raise ValueError so callers can retry or quarantine the output.
    """
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")

    def party(raw, keys):
        if raw is not None and not isinstance(raw, dict):
            raise ValueError("customer and vendor must be JSON objects")
        raw = raw if isinstance(raw, dict) else {}
        return {key: _text(raw.get(key)) for key in keys}

    items = []
    raw_items = value.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError("items must be a JSON array")
    for row_number, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"canonical item row {row_number} must be an object")
        if not any(_text(raw.get(key)) if isinstance(raw.get(key), str) else raw.get(key) is not None
                   for key in ("description", "quantity", "unit_price", "net_amount",
                               "vat_amount", "gross_amount", "amount")):
            raise ValueError(f"canonical item row {row_number} is empty")
        items.append({
            "source_row": raw.get("source_row", row_number),
            "description": _text(raw.get("description")),
            "quantity": _decimal_to_float(parse_amount(raw.get("quantity"))),
            "unit_price": _decimal_to_float(parse_amount(raw.get("unit_price"))),
            "net_amount": _decimal_to_float(parse_amount(raw.get("net_amount"))),
            "vat_amount": _decimal_to_float(parse_amount(raw.get("vat_amount"))),
            "gross_amount": _decimal_to_float(parse_amount(raw.get("gross_amount"))),
            # Existing `amount` is retained verbatim as a deprecated, ambiguous value.
            "amount": _decimal_to_float(parse_amount(raw.get("amount"))),
            "amount_semantics": raw.get("amount_semantics") or (
                "unknown" if raw.get("amount") is not None else None),
            "field_origins": raw.get("field_origins") if isinstance(raw.get("field_origins"), dict) else {},
            "tax_rate_percent": _decimal_to_float(parse_percent(raw.get("tax_rate_percent"))),
            "page": _positive_int(raw.get("page")),
        })
    record = InvoiceRecord(
        invoice_number=_text(value.get("invoice_number")),
        invoice_date=_text(value.get("invoice_date")),
        customer=party(value.get("customer"), ("name", "address", "phone", "email", "tax_id")),
        vendor=party(value.get("vendor"), ("name", "address", "tax_id")),
        items=items,
        total_amount=_decimal_to_float(parse_amount(value.get("total_amount"))),
        subtotal=_decimal_to_float(parse_amount(value.get("subtotal"))),
        tax_amount=_decimal_to_float(parse_amount(value.get("tax_amount"))),
        currency=_text(value.get("currency")),
        bank_account_iban=_text(value.get("bank_account_iban")),
        provenance=provenance or {},
        doc_type=normalize_doc_type(value.get("doc_type")),
        row_count_seen=_non_negative_int(value.get("row_count_seen")),
        extensions=_extensions(value.get("extensions")),
    )
    validate_invoice(record.to_dict())
    return record


def normalize_doc_type(value: Any) -> Optional[str]:
    """Lower-case snake form of a provider classification ("Credit Note" -> credit_note)."""
    text = _text(value)
    return re.sub(r"[\s\-]+", "_", text.casefold()) if text else None


def _positive_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _non_negative_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _extensions(value: Any) -> dict:
    """Keep jurisdiction-specific extras (GSTIN, HSN/SAC, IFSC, ...) as raw scalars.

    They are stored for review only: not validated, not scored and not written to the
    graph until a measured requirement defines their semantics.
    """
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()
            if item is None or isinstance(item, (str, int, float)) and not isinstance(item, bool)}


def arithmetic_warnings(record: dict, tolerance: Optional[float] = None,
                        precision: int = 2) -> list:
    """Check explicitly named amounts; preserve source values and never guess.

    Thin compatibility wrapper over ``validation.arithmetic_findings``.
    """
    from .validation import arithmetic_findings
    return [item["message"] for item in arithmetic_findings(record, precision, tolerance)]


def validation_outcome(record: dict, warnings: list, diagnostics: Optional[dict] = None) -> tuple[str, list]:
    """Classify completeness/reconciliation without treating arithmetic as proof."""
    reasons = list(warnings)
    missing_invoice = [field for field in
                       ("invoice_number", "invoice_date", "subtotal", "tax_amount", "total_amount")
                       if record.get(field) is None]
    if missing_invoice:
        reasons.append(f"Invoice is missing: {', '.join(missing_invoice)}.")
    missing_parties = [role for role in ("vendor", "customer")
                       if not (record.get(role) or {}).get("name")]
    if missing_parties:
        reasons.append(f"Party name not identified: {', '.join(missing_parties)}.")
    if diagnostics and diagnostics.get("generation_may_be_truncated"):
        reasons.append("Model output reached its token limit; the line-item table may be incomplete.")
    items = record.get("items") or []
    if not items:
        reasons.append("No line items were extracted.")
    for index, item in enumerate(items, start=1):
        missing = [field for field in ("quantity", "unit_price", "net_amount", "gross_amount")
                   if item.get(field) is None]
        if missing:
            reasons.append(f"Item {index} is missing: {', '.join(missing)}.")
    for role in ("vendor", "customer"):
        party = record.get(role) or {}
        if party.get("name") and not party.get("address"):
            reasons.append(f"{role.title()} address was not independently identified.")
    if diagnostics and diagnostics.get("generation_may_be_truncated"):
        return "incomplete_extraction", reasons
    if warnings:
        return "arithmetic_mismatch", reasons
    incomplete = (not items or any(any(item.get(field) is None for field in
                      ("quantity", "unit_price", "net_amount", "gross_amount")) for item in items))
    incomplete = incomplete or bool(missing_invoice) or bool(missing_parties)
    if incomplete:
        return "incomplete_extraction", reasons
    if any("address was not independently" in reason for reason in reasons):
        return "ambiguous_review", reasons
    return "valid_reconciled", reasons
