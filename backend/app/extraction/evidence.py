"""Field-level source readings, evidence objects and evidence-match diagnostics.

For every canonical field this module records what the provider actually reported
(``raw_text``) beside the parsed ``value``, plus any page/quote/geometry evidence the
provider returned. When parser or OCR text is available, the quote is compared with
it under tolerant match forms, and the result is stored as a diagnostic.

A mismatch between a visual model's quote and OCR text is a *review finding*, not
proof that the model is wrong: OCR can be the side that misread. Nothing here
changes, fills or corrects an extracted value.
"""

import hashlib
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Optional

from .matching import evidence_form, ocr_fold

FUZZY_THRESHOLD = 0.85
MIN_STRONG_MATCH_CHARS = 3
MAX_FUZZY_SOURCE_CHARS = 200_000
EVIDENCE_METHODS = {"pdf_text", "ocr", "vision_model", "human"}

HEADER_FIELDS = ("invoice_number", "invoice_date", "currency", "bank_account_iban",
                 "subtotal", "tax_amount", "total_amount")
PARTY_FIELDS = {"vendor": ("name", "address", "tax_id"),
                "customer": ("name", "address", "tax_id", "phone", "email")}
ITEM_FIELDS = ("description", "quantity", "unit_price", "net_amount", "vat_amount",
               "gross_amount", "tax_rate_percent")
NUMERIC_FIELDS = {"subtotal", "tax_amount", "total_amount", "quantity", "unit_price",
                  "net_amount", "vat_amount", "gross_amount", "tax_rate_percent"}
MONEY_FIELDS = {"subtotal", "tax_amount", "total_amount", "unit_price", "net_amount",
                "vat_amount", "gross_amount"}

_NUMBER_TOKEN = re.compile(r"\d(?:[\d.,'   ]*\d)?")
_ITEM_ALIAS = re.compile(r"^items\.(\d+)\.(\w+)$")
_NULL_TOKENS = {"", "none", "null", "n/a", "na"}


@dataclass
class ParseContext:
    """What a parser/OCR engine saw. Optional: providers may run without it.

    ``table_row_count`` must come from the parser's own table structure, so it is
    independent of the extraction model's self-reported ``row_count_seen``.
    """
    text: str = ""
    pages: list = field(default_factory=list)  # [{"page": 1, "text": str, "width": n, "height": n}]
    table_row_count: Optional[int] = None
    engine: Optional[str] = None
    engine_version: Optional[str] = None
    table_row_method: Optional[str] = None   # how table_row_count was obtained

    def full_text(self) -> str:
        if self.text:
            return self.text
        return "\n".join(str(page.get("text") or "") for page in self.pages)

    @property
    def page_count(self) -> Optional[int]:
        return len(self.pages) or None

    def page_size(self, page: int):
        for item in self.pages:
            if item.get("page") == page:
                width, height = item.get("width"), item.get("height")
                if _finite(width) and _finite(height) and width > 0 and height > 0:
                    return float(width), float(height)
        return None

    def summary(self) -> dict:
        text = self.full_text()
        return {"engine": self.engine, "engine_version": self.engine_version,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
                "page_count": self.page_count, "table_row_count": self.table_row_count,
                "table_row_method": self.table_row_method,
                "pages": [{"page": p.get("page"), "width": p.get("width"),
                           "height": p.get("height"), "source": p.get("source")}
                          for p in self.pages]}


def _finite(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def item_path(index: int, name: str) -> str:
    """Canonical field path for line items: zero-based index into ``items``."""
    return f"items[{index}].{name}"


def canonical_path(path) -> Optional[str]:
    """Accept ``items.0.quantity`` as an alias of ``items[0].quantity``."""
    if not isinstance(path, str) or not path.strip():
        return None
    path = path.strip()
    alias = _ITEM_ALIAS.match(path)
    return item_path(int(alias.group(1)), alias.group(2)) if alias else path


def _get(obj, *keys):
    for key in keys:
        if isinstance(obj, dict) and obj.get(key) is not None and obj.get(key) != "":
            return obj.get(key)
    return None


def raw_sources(raw: dict, donut_style: bool) -> dict:
    """Map canonical field paths to the provider's untouched output value."""
    sources = {}
    if donut_style:
        from .schema import _sections, unpack_ground_truth
        header, summary, items = _sections(unpack_ground_truth(raw))
        mapping = {
            "invoice_number": _get(header, "invoice_no", "invoice_number"),
            "invoice_date": _get(header, "invoice_date"),
            "bank_account_iban": _get(header, "iban"),
            "total_amount": _get(summary, "total_gross_worth", "invoice_amount"),
            "subtotal": _get(summary, "total_net_worth"),
            "tax_amount": _get(summary, "total_vat"),
            # Dataset party labels combine name and address; the raw reading is the
            # whole label for both derived fields.
            "vendor.name": _get(header, "seller"),
            "vendor.address": _get(header, "seller"),
            "vendor.tax_id": _get(header, "seller_tax_id"),
            "customer.name": _get(header, "client"),
            "customer.address": _get(header, "client"),
            "customer.tax_id": _get(header, "client_tax_id"),
        }
        sources.update({key: value for key, value in mapping.items() if value is not None})
        for index, item in enumerate(items):
            item = item if isinstance(item, dict) else {}
            labels = {
                "description": _get(item, "item_desc", "description"),
                "quantity": _get(item, "item_qty", "quantity"),
                "unit_price": _get(item, "item_net_price", "unit_price"),
                "net_amount": _get(item, "item_net_worth", "total_net_worth", "net_amount"),
                "vat_amount": _get(item, "item_vat_amount", "vat_amount"),
                "gross_amount": _get(item, "item_gross_worth", "gross_amount"),
                "tax_rate_percent": _get(item, "item_vat"),
            }
            sources.update({item_path(index, key): value for key, value in labels.items()
                            if value is not None})
        return sources
    for key in HEADER_FIELDS:
        if raw.get(key) is not None:
            sources[key] = raw[key]
    for role, keys in PARTY_FIELDS.items():
        party = raw.get(role) if isinstance(raw.get(role), dict) else {}
        for key in keys:
            if party.get(key) is not None:
                sources[f"{role}.{key}"] = party[key]
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    for index, item in enumerate(items):
        if isinstance(item, dict):
            for key in ITEM_FIELDS:
                if item.get(key) is not None:
                    sources[item_path(index, key)] = item[key]
    return sources


def record_values(record: dict) -> dict:
    """Map canonical field paths to the typed values stored in the record."""
    values = {key: record.get(key) for key in HEADER_FIELDS}
    for role, keys in PARTY_FIELDS.items():
        party = record.get(role) or {}
        values.update({f"{role}.{key}": party.get(key) for key in keys})
    for index, item in enumerate(record.get("items") or []):
        values.update({item_path(index, key): item.get(key) for key in ITEM_FIELDS})
    return values


def normalize_provider_evidence(evidence) -> dict:
    """Accept {path: quote|{quote,page,bbox}} or [{field, quote, page, bbox}]."""
    entries = {}
    if isinstance(evidence, list):
        evidence = {item.get("field"): item for item in evidence if isinstance(item, dict)}
    if not isinstance(evidence, dict):
        return entries
    for key, value in evidence.items():
        path = canonical_path(key)
        if path is None:
            continue
        if isinstance(value, str):
            value = {"quote": value}
        if not isinstance(value, dict):
            continue
        quote = _get(value, "quote", "snippet", "text")
        entries[path] = {
            "quote": quote if isinstance(quote, str) else None,
            "page": value.get("page"),
            "bbox": _get(value, "bbox", "box", "polygon"),
        }
    return entries


def source_match(quote: Optional[str], source_text: str) -> dict:
    """Locate a quote in parser text under progressively more tolerant forms."""
    if not quote:
        return {"status": "no_quote", "score": None}
    if not source_text:
        return {"status": "no_source_text", "score": None}
    quote_form, source_form = evidence_form(quote), evidence_form(source_text)
    if not quote_form:
        return {"status": "no_quote", "score": None}
    if len(quote_form) < MIN_STRONG_MATCH_CHARS:
        # One- or two-character quotes ("4", "10") match almost any page, so a hit
        # is reported as weak rather than as confirmation.
        found = quote_form in source_form
        return {"status": "too_short", "score": 1.0 if found else 0.0}
    if quote in source_text:
        return {"status": "exact", "score": 1.0}
    if quote_form in source_form:
        return {"status": "normalized", "score": 1.0}
    if ocr_fold(quote_form) in ocr_fold(source_form):
        return {"status": "ocr_folded", "score": 0.97}
    score = _best_window_ratio(ocr_fold(quote_form), ocr_fold(source_form))
    if score < FUZZY_THRESHOLD and "\n" in quote.strip():
        # A multi-line quote (address block, wrapped description) may be interleaved
        # with a neighbouring column in reading-order text: match each line instead.
        lines = [line for line in quote.splitlines() if evidence_form(line)]
        results = [source_match(line, source_text) for line in lines]
        if results and all(r["status"] not in ("not_found", "too_short") for r in results):
            return {"status": "lines", "score": round(min(r["score"] for r in results), 4)}
    return {"status": "fuzzy" if score >= FUZZY_THRESHOLD else "not_found",
            "score": round(score, 4)}


def _best_window_ratio(needle: str, haystack: str) -> float:
    if not haystack or len(haystack) > MAX_FUZZY_SOURCE_CHARS:
        return 0.0
    size = len(needle)
    if len(haystack) <= size:
        return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()
    best = 0.0
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(needle)
    for width in {size - 1, size, size + 1} - {0}:
        for start in range(0, len(haystack) - width + 1):
            matcher.set_seq1(haystack[start:start + width])
            if matcher.real_quick_ratio() <= best or matcher.quick_ratio() <= best:
                continue
            best = max(best, matcher.ratio())
            if best == 1.0:
                return best
    return best


def value_in_quote(path: str, value, quote: Optional[str]):
    """True/False when a quote exists, otherwise None. Never alters ``value``."""
    if not quote or value is None:
        return None
    name = path.rsplit(".", 1)[-1]
    if name in NUMERIC_FIELDS and isinstance(value, (int, float)):
        from .schema import parse_amount
        target = Decimal(str(value))
        for token in _NUMBER_TOKEN.findall(quote):
            parsed = parse_amount(token.replace(" ", "").replace(" ", "")
                                  .replace("'", "").replace(" ", ""))
            if parsed is not None and parsed == target:
                return True
        return False
    value_form, quote_form = evidence_form(value), evidence_form(quote)
    if not value_form:
        return None
    return value_form in quote_form or ocr_fold(value_form) in ocr_fold(quote_form)


def validate_page(page, parse: Optional[ParseContext]) -> Optional[str]:
    if page is None:
        return None
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return "page must be a positive integer"
    if parse and parse.page_count and page > parse.page_count:
        return f"page {page} exceeds parsed page count {parse.page_count}"
    return None


def validate_geometry(bbox, page, parse: Optional[ParseContext]) -> Optional[str]:
    """Accept [x0, y0, x1, y1] or a polygon [[x, y], ...]; pixels or 0..1 units."""
    if bbox is None:
        return None
    points = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(_finite(v) for v in bbox):
        x0, y0, x1, y1 = (float(v) for v in bbox)
        if not (x0 < x1 and y0 < y1):
            return "bbox must satisfy x0 < x1 and y0 < y1"
        points = [(x0, y0), (x1, y1)]
    elif (isinstance(bbox, (list, tuple)) and len(bbox) >= 3 and
          all(isinstance(p, (list, tuple)) and len(p) == 2 and all(_finite(v) for v in p)
              for p in bbox)):
        points = [(float(x), float(y)) for x, y in bbox]
    else:
        return "bbox must be [x0, y0, x1, y1] or a polygon of [x, y] points"
    if any(x < 0 or y < 0 for x, y in points):
        return "bbox has negative coordinates"
    if all(x <= 1 and y <= 1 for x, y in points):
        return None  # normalized coordinates are within the unit page
    size = parse.page_size(page) if parse and isinstance(page, int) else None
    if size and any(x > size[0] or y > size[1] for x, y in points):
        return f"bbox lies outside page {page} ({size[0]:g}x{size[1]:g})"
    return None


def build_field_evidence(record: dict, raw: dict, donut_style: bool,
                         parse: Optional[ParseContext] = None,
                         method: str = "vision_model"):
    """Return ({path: evidence}, findings) for every non-null or raw-reported field."""
    from .validation import finding

    method = method if method in EVIDENCE_METHODS else "vision_model"
    provider_evidence = normalize_provider_evidence(raw.get("evidence"))
    sources = raw_sources(raw, donut_style)
    values = record_values(record)
    source_text = parse.full_text() if parse else ""
    entries, findings = {}, []
    for path in sorted(set(values) | set(sources), key=_path_order):
        value = values.get(path)
        raw_value = sources.get(path)
        if value is None and raw_value is None:
            continue
        name = path.rsplit(".", 1)[-1]
        row = _row(path)
        raw_text = raw_value if isinstance(raw_value, str) else None
        if (value is None and name in NUMERIC_FIELDS and raw_text is not None
                and raw_text.strip().casefold() not in _NULL_TOKENS):
            findings.append(finding("unparseable_value", f"{path}: provider reported "
                                    f"{raw_text!r}, which is not an unambiguous number; "
                                    "stored as null.", field=path, row=row))
        given = provider_evidence.get(path, {})
        quote, page, bbox = given.get("quote"), given.get("page"), given.get("bbox")
        match = {"source": source_match(quote, source_text) if quote else
                 {"status": "no_quote", "score": None},
                 "value_in_quote": value_in_quote(path, value, quote),
                 "quote_form": evidence_form(quote) if quote else None}
        page_error, geometry_error = validate_page(page, parse), validate_geometry(bbox, page, parse)
        entries[path] = {"raw_text": raw_text,
                         "raw_type": None if raw_value is None else type(raw_value).__name__,
                         "value": value, "page": page, "quote": quote, "bbox": bbox,
                         "method": method, "match": match}
        if page_error:
            findings.append(finding("evidence_page_invalid", f"{path}: {page_error}.",
                                    field=path, row=row))
        if geometry_error:
            findings.append(finding("evidence_geometry_invalid", f"{path}: {geometry_error}.",
                                    field=path, row=row))
        if match["source"]["status"] == "not_found":
            findings.append(finding(
                "evidence_not_in_source",
                f"{path}: quoted evidence was not found in the parser text (best score "
                f"{match['source']['score']}). The model and OCR disagree; review the image.",
                field=path, row=row))
        if match["value_in_quote"] is False:
            findings.append(finding("value_not_in_quote",
                                    f"{path}: value {value!r} does not appear in its quoted "
                                    f"evidence {quote!r}.", field=path, row=row))
    if not provider_evidence:
        findings.append(finding("evidence_missing",
                                "Provider returned no field-level evidence; values are not "
                                "linked to a page, quote or region."))
    return entries, findings


def money_precision(field_evidence: dict, default: int = 2, maximum: int = 4) -> int:
    """Decimal places printed on the invoice's money values (from raw readings)."""
    places = []
    for path, entry in field_evidence.items():
        raw_text = entry.get("raw_text")
        if path.rsplit(".", 1)[-1] not in MONEY_FIELDS or not raw_text:
            continue
        tokens = _NUMBER_TOKEN.findall(raw_text)
        if not tokens:
            continue
        token = tokens[-1]
        mark = max(token.rfind("."), token.rfind(","))
        if mark >= 0 and len(token) - mark - 1 != 3:
            places.append(len(token) - mark - 1)
        elif mark < 0:
            places.append(0)
    return min(max(places), maximum) if places else default


def _row(path: str) -> Optional[int]:
    match = re.match(r"^items\[(\d+)\]", path)
    return int(match.group(1)) + 1 if match else None


def _path_order(path: str):
    row = _row(path)
    return (row is not None, row or 0, path)
