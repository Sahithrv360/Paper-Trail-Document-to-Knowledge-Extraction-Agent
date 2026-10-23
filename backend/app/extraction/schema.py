"""Canonical invoice fields and safe normalization for Donut annotations."""

import re
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional


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
    # Currency codes/symbols are intentionally not inferred by this parser.
    text = re.sub(r"[^0-9,.()\-]", "", text)
    if text.startswith("(") and text.endswith(")"):
        return None  # Negative invoice totals are not accepted as a positive total.
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
        vat_derived = False
        if vat is None and net is not None and gross is not None:
            vat = gross - net
            vat_derived = True
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
            "field_origins": {"vat_amount": "derived_from_printed_net_and_gross"
                              if vat_derived else "model_output" if vat is not None else "unknown"},
            "tax_rate_percent": _decimal_to_float(parse_percent(item.get("item_vat"))),
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
    for key in ("invoice_number", "invoice_date", "currency"):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ValueError(f"{key} must be a string or null")
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
        if not isinstance(item, dict) or not isinstance(item.get("description"), str):
            raise ValueError("each item requires a description")
        if "source_row" in item and (isinstance(item["source_row"], bool) or
                                      not isinstance(item["source_row"], int) or
                                      item["source_row"] < 1):
            raise ValueError("item source_row must be a positive integer")
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
        if not isinstance(raw, dict) or not _text(raw.get("description")):
            raise ValueError(f"canonical item row {row_number} requires a description")
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
    )
    validate_invoice(record.to_dict())
    return record


def arithmetic_warnings(record: dict, tolerance: float = 0.02) -> list:
    """Check explicitly named amounts; preserve source values and never guess."""
    warnings = []
    cents = Decimal("0.01")

    def money(value):
        return Decimal(str(value)).quantize(cents, rounding=ROUND_HALF_UP)

    sums = {"net_amount": Decimal("0"), "vat_amount": Decimal("0"),
            "gross_amount": Decimal("0")}
    complete = {key: True for key in sums}
    for index, item in enumerate(record.get("items", []), start=1):
        q, price = item.get("quantity"), item.get("unit_price")
        net, vat, gross = (item.get("net_amount"), item.get("vat_amount"),
                           item.get("gross_amount"))
        rate = item.get("tax_rate_percent")
        if q is not None and price is not None and net is not None:
            expected = money(Decimal(str(q)) * Decimal(str(price)))
            if abs(expected - money(net)) > Decimal(str(tolerance)):
                warnings.append(f"Item {index}: net amount {money(net)} differs from quantity × net unit price ({expected}).")
        if net is not None and rate is not None and vat is not None:
            expected = money(Decimal(str(net)) * Decimal(str(rate)) / Decimal("100"))
            if abs(expected - money(vat)) > Decimal(str(tolerance)):
                warnings.append(f"Item {index}: VAT amount {money(vat)} differs from net amount × VAT rate ({expected}).")
        if net is not None and vat is not None and gross is not None:
            expected = money(Decimal(str(net)) + Decimal(str(vat)))
            if abs(expected - money(gross)) > Decimal(str(tolerance)):
                warnings.append(f"Item {index}: gross amount {money(gross)} differs from net plus VAT ({expected}).")
        for key, value in (("net_amount", net), ("vat_amount", vat), ("gross_amount", gross)):
            if value is None:
                complete[key] = False
            else:
                sums[key] += money(value)

    for key, invoice_key, label in (("net_amount", "subtotal", "net subtotal"),
                                    ("vat_amount", "tax_amount", "VAT total"),
                                    ("gross_amount", "total_amount", "gross total")):
        expected = record.get(invoice_key)
        if expected is not None and complete[key] and abs(sums[key] - money(expected)) > Decimal(str(tolerance)):
            warnings.append(f"Line-item {key.replace('_', ' ')} sum {sums[key]} differs from invoice {label} {money(expected)}.")

    sub, tax, total = record.get("subtotal"), record.get("tax_amount"), record.get("total_amount")
    if None not in (sub, tax, total) and abs(money(sub) + money(tax) - money(total)) > Decimal(str(tolerance)):
        warnings.append(f"Invoice net subtotal plus VAT ({money(sub) + money(tax)}) differs from gross total ({money(total)}).")
    return warnings


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
