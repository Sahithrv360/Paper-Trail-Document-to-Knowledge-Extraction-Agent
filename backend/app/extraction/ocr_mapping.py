"""Map OCR fragments (text + boxes) to the canonical invoice shape without a model.

This is a transparent, rule-based reader for invoices with printed field labels and a
column-headed item table. It uses only generic English labels ("Invoice no",
"Seller", "Client", "Tax Id", "IBAN", "Description", "Qty", ...) and column geometry.
It contains no expected values. Every value it reports is the OCR text as read
(including OCR mistakes, which are not corrected), with the quote, page and box it
came from, so validation and the reviewer can see exactly where it was read.

Limitations: English labels only; one item table; party blocks must be labelled. It
cannot recover text the OCR engine missed. Because its evidence quotes come from the
same OCR text, an evidence match here proves traceability, not correctness.
"""

import re

from .local_ocr import group_rows

RULES_VERSION = "ocr-layout-rules-2"

_NUMBER = re.compile(r"^[\(\-]?\d[\d.,' ]*\d?%?\)?$|^\d%?$")
_CURRENCY = re.compile(r"^(\$|€|£|¥|₹|USD|EUR|GBP|INR|JPY|CHF|PLN|AUD|CAD)$", re.I)
_ROW_NUMBER = re.compile(r"^\d{1,3}[.):;,]?$")
_DATE = re.compile(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{2,4}|"
                   r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{2,4}")

INVOICE_NO = re.compile(r"\binvoice\s*(?:no|number|num|nr|#)\b\.?\s*[:#.]?\s*", re.I)
DATE_LABEL = re.compile(r"\b(?:date\s+of\s+issue|invoice\s+date|issue\s+date|issued\s+on|date)\b"
                        r"\s*[:.]?\s*", re.I)
IBAN_LABEL = re.compile(r"\biban\b\s*[:.]?\s*", re.I)
TAX_LABEL = re.compile(r"\b(?:tax\s*id|vat\s*(?:id|no|number|reg(?:istration)?\s*no)|tin|"
                       r"gstin|nip|ein|abn)\b\s*[:.#]?\s*", re.I)
SELLER_LABEL = re.compile(r"^(?:seller|vendor|supplier|from|sold\s+by|issued\s+by)\s*:?$", re.I)
CLIENT_LABEL = re.compile(r"^(?:client|customer|buyer|bill(?:ed)?\s+to|sold\s+to|invoice\s+to)\s*:?$",
                          re.I)
BLOCK_END = re.compile(r"\b(tax\s*id|vat\s*(id|no)|iban|items|description|invoice\s*no|"
                       r"date\s+of\s+issue|summary)\b", re.I)
TABLE_HEADER = re.compile(r"\bdescription\b.*\b(qty|quantity|price|amount|net|worth|total)\b", re.I)
TABLE_END = re.compile(r"^(summary|sub\s*-?\s*total|total|net\s+total|grand\s+total|"
                       r"amount\s+due|balance\s+due)\b", re.I)

# Header label -> canonical column. Order matters: more specific labels first.
COLUMN_LABELS = [
    ("index", re.compile(r"^(no\.?|#|nr\.?|lp\.?|item\s*no\.?)$", re.I)),
    ("description", re.compile(r"\b(description|product|article|service|details)\b|^items?$", re.I)),
    ("quantity", re.compile(r"\b(qty|quantity|qnty)\b", re.I)),
    ("unit", re.compile(r"^(um|uom|unit|units)$", re.I)),
    ("tax_rate_percent", re.compile(r"\b(vat|tax|gst)\b.*%|%|\b(vat|tax)\s*rate\b", re.I)),
    ("unit_price", re.compile(r"\b(net\s+price|unit\s+price|price|rate)\b", re.I)),
    ("net_amount", re.compile(r"\b(net\s+(worth|amount|value|total))\b", re.I)),
    ("vat_amount", re.compile(r"\b(vat|tax|gst)(\s+amount)?\b", re.I)),
    ("gross_amount", re.compile(r"\b(gross(\s+(worth|amount|value))?|total|amount)\b", re.I)),
]
NUMERIC_COLUMNS = {"quantity", "unit_price", "net_amount", "vat_amount", "gross_amount",
                   "tax_rate_percent"}


def _union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [round(min(b[0] for b in boxes), 1), round(min(b[1] for b in boxes), 1),
            round(max(b[2] for b in boxes), 1), round(max(b[3] for b in boxes), 1)]


_GROUPED_NUMBER = re.compile(r"^\d{1,3}(?:[ \u00a0\u202f']\d{3})+(?:[.,]\d{1,4})?%?$")


def _coalesce_grouped_number(tokens):
    """Join OCR words only when they form an unambiguous grouped number.

    Tesseract can emit an amount such as ``33 319,74`` as two adjacent tokens.
    Arbitrary multiple values remain ambiguous; only a valid three-digit grouping
    pattern is combined.
    """
    if len(tokens) < 2:
        return tokens
    ordered = sorted(tokens, key=lambda token: token["bbox"][0])
    text = " ".join(token["text"] for token in ordered)
    if not _GROUPED_NUMBER.fullmatch(text):
        return tokens
    bbox = _union([token["bbox"] for token in ordered])
    return [{"text": text, "bbox": bbox, "cx": (bbox[0] + bbox[2]) / 2.0}]


def _coalesce_adjacent_grouped_numbers(tokens):
    """Rejoin nearby word tokens before summary-column matching.

    A thousands-group prefix can fall just outside its summary column even though
    it is visibly adjacent to the rest of that amount. Restrict merging by both
    numeric shape and OCR-box distance so separate summary values stay separate.
    """
    groups = []
    for token in sorted(tokens, key=lambda value: value["bbox"][0]):
        if groups:
            prior = groups[-1][-1]
            gap = token["bbox"][0] - prior["bbox"][2]
            height = max(prior["bbox"][3] - prior["bbox"][1],
                         token["bbox"][3] - token["bbox"][1], 1.0)
            candidate = groups[-1] + [token]
            candidate_text = " ".join(part["text"] for part in candidate)
            if -1.0 <= gap <= 1.5 * height and _GROUPED_NUMBER.fullmatch(candidate_text):
                groups[-1].append(token)
                continue
        groups.append([token])
    return [_coalesce_grouped_number(group)[0] if len(group) > 1 else group[0]
            for group in groups]


def _tokens(fragment):
    """Split a fragment into tokens with x-centers interpolated across its box."""
    text, box = fragment["text"], fragment["bbox"]
    width = max(box[2] - box[0], 1.0)
    length = max(len(text), 1)
    tokens = []
    for match in re.finditer(r"\S+", text):
        x0 = box[0] + width * match.start() / length
        x1 = box[0] + width * match.end() / length
        tokens.append({"text": match.group(0), "bbox": [x0, box[1], x1, box[3]],
                       "cx": (x0 + x1) / 2.0})
    return tokens


def _row_text(row):
    return "  ".join(f["text"] for f in row["fragments"])


def _evidence(page, quote_parts, boxes, separator=" "):
    quote = separator.join(part for part in quote_parts if part).strip()
    return {"quote": quote or None, "page": page, "bbox": _union(boxes)}


class _Reader:
    def __init__(self, parse):
        self.pages = []
        for page in parse.pages:
            rows = group_rows(page.get("fragments") or [])
            heights = sorted(r["bottom"] - r["top"] for r in rows) or [10.0]
            self.pages.append({"page": page.get("page"), "rows": rows,
                               "row_height": heights[len(heights) // 2]})
        self.output = {"vendor": {}, "customer": {}, "items": []}
        self.evidence = {}
        self.reasons = []

    def put(self, path, value, page, quote_parts, boxes, separator=" "):
        value = (value or "").strip()
        if not value:
            return
        target = self.output
        parts = path.split(".")
        for key in parts[:-1]:
            target = target[key]
        target[parts[-1]] = value
        self.evidence[path] = _evidence(page, quote_parts, boxes, separator)

    # -- header fields -------------------------------------------------------------

    def labelled_value(self, path, label, value_pattern=None, first_page_only=True):
        for page in self.pages[:1] if first_page_only else self.pages:
            for row in page["rows"]:
                for index, fragment in enumerate(row["fragments"]):
                    match = label.search(fragment["text"])
                    if not match:
                        continue
                    rest = fragment["text"][match.end():].strip()
                    used = [fragment]
                    if not rest and index + 1 < len(row["fragments"]):
                        used.append(row["fragments"][index + 1])
                        rest = row["fragments"][index + 1]["text"].strip()
                    if value_pattern is not None:
                        found = value_pattern.search(rest)
                        rest = found.group(0) if found else ""
                    else:
                        rest = rest.split("  ")[0]
                    if rest:
                        self.put(path, rest, page["page"], [f["text"] for f in used],
                                 [f["bbox"] for f in used[-1:]])
                        return True
        return False

    def header(self):
        self.labelled_value("invoice_number", INVOICE_NO, re.compile(r"^[A-Za-z0-9][\w\-/.]*"))
        self.labelled_value("invoice_date", DATE_LABEL, _DATE)
        self.labelled_value("bank_account_iban", IBAN_LABEL, re.compile(r"^[A-Za-z]{2}[\dA-Za-z ]{10,40}"))
        first = self.pages[0]["rows"] if self.pages else []
        text = " ".join(_row_text(r) for r in first[:12]).casefold()
        if re.search(r"\binvoice\b", text):
            self.output["doc_type"] = "invoice"

    # -- parties ---------------------------------------------------------------------

    def parties(self):
        if not self.pages:
            return
        page = self.pages[0]
        rows, row_height = page["rows"], page["row_height"]
        for r_index, row in enumerate(rows):
            seller = next((f for f in row["fragments"] if SELLER_LABEL.match(f["text"].strip())), None)
            client = next((f for f in row["fragments"] if CLIENT_LABEL.match(f["text"].strip())), None)
            if seller and client:
                left_is_seller = seller["bbox"][0] < client["bbox"][0]
                boundary = (client if left_is_seller else seller)["bbox"][0] - row_height
                columns = {"vendor": (left_is_seller, boundary), "customer": (not left_is_seller, boundary)}
                block = self._block(rows, r_index + 1, row_height)
                for role, (is_left, edge) in columns.items():
                    self._party_from_block(role, block, page["page"],
                                           lambda f, l=is_left, e=edge: (f["bbox"][0] < e) == l)
                self._tax_ids(rows[r_index + 1:], page["page"], boundary,
                              "vendor" if left_is_seller else "customer",
                              "customer" if left_is_seller else "vendor")
                return
        # Stacked layout: "Seller:" block above "Client:" block, single column.
        for role, label in (("vendor", SELLER_LABEL), ("customer", CLIENT_LABEL)):
            for r_index, row in enumerate(rows):
                if row["fragments"] and label.match(row["fragments"][0]["text"].strip()):
                    block = self._block(rows, r_index + 1, row_height)
                    self._party_from_block(role, block, page["page"], lambda f: True)
                    for tax_row in rows[r_index + 1:r_index + 1 + len(block) + 2]:
                        if self._tax_from_row(role, tax_row, page["page"], lambda f: True):
                            break
                    break
        if not self.output["vendor"] and not self.output["customer"]:
            self.reasons.append("No labelled seller/client blocks were found by the OCR layout rules.")

    def _block(self, rows, start, row_height):
        block, previous_bottom = [], None
        for row in rows[start:]:
            text = _row_text(row)
            if BLOCK_END.search(text) or SELLER_LABEL.match(text.strip()) or CLIENT_LABEL.match(text.strip()):
                break
            if previous_bottom is not None and row["top"] - previous_bottom > 2.5 * row_height:
                break
            block.append(row)
            previous_bottom = row["bottom"]
        return block

    def _party_from_block(self, role, block, page, in_column):
        lines = []
        for row in block:
            fragments = [f for f in row["fragments"] if in_column(f)]
            if fragments:
                lines.append(fragments)
        if not lines:
            return
        self.put(f"{role}.name", " ".join(f["text"] for f in lines[0]), page,
                 [f["text"] for f in lines[0]], [f["bbox"] for f in lines[0]])
        if len(lines) > 1:
            address = "\n".join(" ".join(f["text"] for f in line) for line in lines[1:])
            flat = [f for line in lines[1:] for f in line]
            self.put(f"{role}.address", address, page,
                     [" ".join(f["text"] for f in line) for line in lines[1:]],
                     [f["bbox"] for f in flat], separator="\n")

    def _tax_from_row(self, role, row, page, in_column):
        for fragment in row["fragments"]:
            if not in_column(fragment):
                continue
            match = TAX_LABEL.search(fragment["text"])
            if match:
                value = fragment["text"][match.end():].strip()
                if value:
                    self.put(f"{role}.tax_id", value.split()[0], page, [fragment["text"]],
                             [fragment["bbox"]])
                    return True
        return False

    def _tax_ids(self, rows, page, boundary, left_role, right_role):
        for row in rows[:12]:
            left = self._tax_from_row(left_role, row, page, lambda f: f["bbox"][0] < boundary)
            right = self._tax_from_row(right_role, row, page, lambda f: f["bbox"][0] >= boundary)
            if left or right:
                return

    # -- items -------------------------------------------------------------------------

    def items(self):
        columns, page_seen = None, False
        for page in self.pages:
            rows = page["rows"]
            start = None
            for index, row in enumerate(rows):
                if TABLE_HEADER.search(_row_text(row)):
                    start = index
                    break
            if start is None:
                if page_seen and columns:
                    self.reasons.append(f"Page {page['page']} has no repeated table header; "
                                        "rows there were not read.")
                continue
            page_seen = True
            columns, consumed = self._columns(rows, start, page["row_height"])
            self._table_rows(rows[start + 1 + consumed:], columns, page)
        if not page_seen:
            self.reasons.append("No item table header (Description + Qty/Price/Amount) was found.")

    def _columns(self, rows, start, row_height):
        header = list(rows[start]["fragments"])
        consumed = 0
        # A two-line header ("Gross" / "worth") continues directly below without digits.
        if start + 1 < len(rows):
            below = rows[start + 1]
            if (below["top"] - rows[start]["bottom"] < 0.8 * row_height
                    and not any(re.search(r"\d", f["text"]) for f in below["fragments"])):
                consumed = 1
                for fragment in below["fragments"]:
                    above = min(header, key=lambda h: abs((h["bbox"][0] + h["bbox"][2]) / 2
                                                          - (fragment["bbox"][0] + fragment["bbox"][2]) / 2))
                    above["text"] = above["text"] + " " + fragment["text"]
                    above["bbox"] = _union([above["bbox"], fragment["bbox"]])
        labels = []
        for fragment in header:
            for token_group in self._split_header(fragment):
                labels.append(token_group)
        columns = []
        for label in labels:
            kind = next((name for name, pattern in COLUMN_LABELS if pattern.search(label["text"])), None)
            if kind is None:
                continue
            columns.append({"kind": kind, "label": label["text"], "x0": label["bbox"][0],
                            "x1": label["bbox"][2], "cx": (label["bbox"][0] + label["bbox"][2]) / 2})
        columns.sort(key=lambda c: c["x0"])
        return columns, consumed

    @staticmethod
    def _split_header(fragment):
        """Split a merged header fragment ("No. Description") at known label words."""
        tokens = _tokens(fragment)
        groups, current = [], []
        for token in tokens:
            starts_new = re.match(r"^(no\.?|#|description|qty|quantity|um|uom|unit|net|vat|tax|"
                                  r"gross|price|amount|total|product|item)", token["text"], re.I)
            continuation = current and re.match(r"^(price|worth|amount|value|\[%\]|%|rate|no\.?)$",
                                                token["text"], re.I)
            if starts_new and current and not continuation:
                groups.append(current)
                current = []
            current.append(token)
        if current:
            groups.append(current)
        return [{"text": " ".join(t["text"] for t in group),
                 "bbox": [group[0]["bbox"][0], fragment["bbox"][1], group[-1]["bbox"][2],
                          fragment["bbox"][3]]} for group in groups]

    def _assign(self, token, columns):
        """Column for a token: description/index by left boundaries, numbers by nearest center."""
        numeric = [c for c in columns if c["kind"] in NUMERIC_COLUMNS]
        if not numeric:
            return None
        first_numeric = min(c["x0"] for c in numeric)
        text_columns = [c for c in columns if c["kind"] in ("index", "description")]
        if token["cx"] < first_numeric - 0.25 * (numeric[0]["x1"] - numeric[0]["x0"]):
            if not text_columns:
                return None
            for column in reversed(text_columns):
                if token["cx"] >= column["x0"] - 5 or column is text_columns[0]:
                    return column["kind"]
        candidates = [c for c in columns if c["kind"] in NUMERIC_COLUMNS | {"unit"}]
        return min(candidates, key=lambda c: abs(c["cx"] - token["cx"]))["kind"]

    def _table_rows(self, rows, columns, page):
        has_index = any(c["kind"] == "index" for c in columns)
        current = None
        for row in rows:
            text = _row_text(row).strip()
            if TABLE_END.search(text):
                break
            assigned = {}
            for fragment in row["fragments"]:
                for token in _tokens(fragment):
                    kind = self._assign(token, columns)
                    if kind is None or kind == "unit":
                        continue
                    if kind == "index" and not _ROW_NUMBER.match(token["text"]):
                        kind = "description"
                    if kind in NUMERIC_COLUMNS and not _NUMBER.match(token["text"]):
                        continue  # unit words ("each") or currency marks beside amounts
                    assigned.setdefault(kind, []).append(token)
            for kind in NUMERIC_COLUMNS:
                if len(assigned.get(kind, [])) > 1:
                    assigned[kind] = _coalesce_grouped_number(assigned[kind])
            numeric_columns = sum(1 for k in assigned if k in NUMERIC_COLUMNS)
            starts_row = (bool(assigned.get("index")) or numeric_columns >= 2 if has_index
                          else numeric_columns >= 1)
            if starts_row:
                if has_index and not assigned.get("index"):
                    self.reasons.append(f"Item {len(self.output['items']) + 1}: its row number was "
                                        "not read; the row was started from its amounts.")
                current = {"tokens": assigned, "page": page["page"]}
                self._emit(current)
            elif current is not None and assigned.get("description") and not any(
                    k in NUMERIC_COLUMNS for k in assigned):
                self._continue_description(assigned["description"])
            elif any(k in NUMERIC_COLUMNS for k in assigned):
                self.reasons.append(f"Page {page['page']}: an amount row without a row number "
                                    f"was not attached to any item ({text[:60]!r}).")

    def _emit(self, row):
        tokens = row["tokens"]
        index = len(self.output["items"])
        item = {"page": row["page"]}
        self.output["items"].append(item)
        for kind, values in tokens.items():
            if kind == "index":
                continue
            if kind in NUMERIC_COLUMNS and len(values) > 1:
                self.reasons.append(f"Item {index + 1}: several values fall under the "
                                    f"{kind} column; none was used.")
                continue
            text = " ".join(t["text"] for t in values)
            item[kind] = text
            self.evidence[f"items[{index}].{kind}"] = _evidence(
                row["page"], [text], [t["bbox"] for t in values])
        if "description" not in item:
            self.reasons.append(f"Item {index + 1}: no description was read by OCR.")

    def _continue_description(self, tokens):
        item = self.output["items"][-1]
        index = len(self.output["items"]) - 1
        addition = " ".join(t["text"] for t in tokens)
        item["description"] = ((item.get("description") + "\n") if item.get("description") else "") + addition
        path = f"items[{index}].description"
        previous = self.evidence.get(path, {"quote": "", "page": item["page"], "bbox": None})
        self.evidence[path] = {"quote": (previous["quote"] + "\n" + addition).strip(),
                               "page": previous["page"],
                               "bbox": _union([previous["bbox"]] + [t["bbox"] for t in tokens])}

    # -- totals --------------------------------------------------------------------------

    def totals(self):
        for page in reversed(self.pages):
            rows = page["rows"]
            for index, row in enumerate(rows):
                fragments = row["fragments"]
                if not fragments:
                    continue
                label = fragments[0]["text"].strip()
                if not re.match(r"^(total|grand\s+total|amount\s+due|balance\s+due)\b", label, re.I):
                    continue
                amounts = [t for f in fragments for t in _tokens(f)
                           if _NUMBER.match(t["text"]) and not t["text"].endswith("%")]
                amounts = _coalesce_adjacent_grouped_numbers(amounts)
                currencies = {t["text"] for f in fragments for t in _tokens(f) if _CURRENCY.match(t["text"])}
                header = self._summary_header(rows[:index])
                if header and len(amounts) > 1:
                    grouped = {}
                    for token in amounts:
                        column = min(header, key=lambda c: abs(c["cx"] - token["cx"]))
                        width = max(column["width"], token["bbox"][2] - token["bbox"][0])
                        if abs(column["cx"] - token["cx"]) > width:
                            self.reasons.append(f"Total-line amount {token['text']!r} is not under "
                                                "a labelled summary column; it was not assigned.")
                            continue
                        target = {"net_amount": "subtotal", "vat_amount": "tax_amount",
                                  "gross_amount": "total_amount"}.get(column["kind"])
                        if target:
                            grouped.setdefault(target, []).append(token)
                    for target, tokens in grouped.items():
                        merged = _coalesce_grouped_number(tokens)
                        if len(merged) != 1:
                            self.reasons.append(f"Total-line {target.replace('_', ' ')} has "
                                                "multiple values in one summary column; none "
                                                "was assigned.")
                        elif target not in self.output:
                            self.put(target, merged[0]["text"], page["page"],
                                     [_row_text(row)], [merged[0]["bbox"]])
                elif len(amounts) == 1:
                    self.put("total_amount", amounts[0]["text"], page["page"], [_row_text(row)],
                             [amounts[0]["bbox"]])
                elif amounts:
                    self.reasons.append("The total line has several amounts without column labels; "
                                        "none was assigned.")
                if len(currencies) == 1 and "total_amount" in self.output:
                    self.put("currency", currencies.pop(), page["page"], [_row_text(row)], [])
                return

    @staticmethod
    def _summary_header(rows):
        for row in reversed(rows[-4:]):
            labels = []
            for fragment in row["fragments"]:
                labels.extend(_Reader._split_header(fragment))
            columns = []
            for label in labels:
                text = label["text"]
                kind = ("net_amount" if re.search(r"\bnet\b", text, re.I) else
                        "gross_amount" if re.search(r"\bgross\b", text, re.I) else
                        "vat_amount" if re.search(r"\b(vat|tax)\b", text, re.I) and "%" not in text
                        else None)
                if kind:
                    columns.append({"kind": kind, "cx": (label["bbox"][0] + label["bbox"][2]) / 2,
                                    "width": label["bbox"][2] - label["bbox"][0]})
            if len(columns) >= 2:
                return columns
        return None


def map_parse_to_invoice(parse) -> dict:
    """Return a canonical-shape dict with string values, evidence and review reasons."""
    reader = _Reader(parse)
    reader.header()
    reader.parties()
    reader.items()
    reader.totals()
    output = reader.output
    output["evidence"] = reader.evidence
    output["review_reasons"] = reader.reasons
    return output


class OcrLayoutProvider:
    """Local, model-free provider: OCR text + layout rules -> canonical fields."""
    name = "ocr-layout"
    revision = RULES_VERSION
    consumes_prompt = False
    accepts_document = True
    extraction_method = "ocr"

    def extract_document(self, document, context=None, parse=None) -> dict:
        if parse is None:
            raise ValueError("ocr-layout provider needs parser output (PARSER_ENGINE)")
        return map_parse_to_invoice(parse)

    def extract(self, image, context=None):  # pragma: no cover - guard for misuse
        raise ValueError("ocr-layout provider needs a parsed document, not a bare image")
