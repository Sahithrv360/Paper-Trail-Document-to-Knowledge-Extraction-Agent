"""Field-level exact-match metrics for small invoice extraction runs."""

from decimal import Decimal, InvalidOperation

from .schema import normalize_donut_result


FIELD_PATHS = {
    "invoice_number": ("invoice_number",),
    "invoice_date": ("invoice_date",),
    "customer": ("customer", "name"),
    "customer_tax_id": ("customer", "tax_id"),
    "vendor": ("vendor", "name"),
    "vendor_tax_id": ("vendor", "tax_id"),
    "bank_account_iban": ("bank_account_iban",),
    "total_amount": ("total_amount",),
    "subtotal": ("subtotal",),
    "tax_amount": ("tax_amount",),
    "line_item_descriptions": ("items", "description"),
    "line_item_quantities": ("items", "quantity"),
    "line_item_unit_prices": ("items", "unit_price"),
    "line_item_net_amounts": ("items", "net_amount"),
    "line_item_vat_amounts": ("items", "vat_amount"),
    "line_item_gross_amounts": ("items", "gross_amount"),
    # Deprecated compatibility metric. The historical `amount` label used a
    # gross-first fallback and therefore has no single meaning across old rows.
    "line_item_amounts": ("items", "amount"),
    "line_item_tax_rate_percent": ("items", "tax_rate_percent"),
}


def _value(record: dict, path: tuple):
    value = record
    for key in path:
        if key == "items":
            item_field = path[1] if len(path) > 1 else "description"
            items = value.get("items") if isinstance(value, dict) else None
            if not isinstance(items, list):
                return None
            return [item.get(item_field) if isinstance(item, dict) else None for item in items]
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _normalized(value):
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


def _equal(field: str, predicted, expected) -> bool:
    if field == "total_amount":
        try:
            return Decimal(str(predicted)) == Decimal(str(expected))
        except (InvalidOperation, ValueError):
            return False
    return _normalized(predicted) == _normalized(expected)


def evaluate_predictions(rows: list[dict]) -> dict:
    """Score only labels present in the dataset; also report prediction coverage."""
    counts = {field: {"scored": 0, "correct": 0, "predicted": 0}
              for field in FIELD_PATHS}
    document_correct = 0
    for row in rows:
        expected = normalize_donut_result(row["ground_truth"]).to_dict()
        predicted = row["prediction"]
        example_exact = True
        example_scored = False
        for field, path in FIELD_PATHS.items():
            gold = _value(expected, path)
            if gold is None or gold == []:
                continue
            example_scored = True
            prediction = _value(predicted, path)
            counts[field]["scored"] += 1
            if prediction is not None and prediction != []:
                counts[field]["predicted"] += 1
            if _equal(field, prediction, gold):
                counts[field]["correct"] += 1
            else:
                example_exact = False
        if example_exact and example_scored:
            document_correct += 1
    metrics = {}
    for field, values in counts.items():
        scored = values["scored"]
        metrics[field] = {
            **values,
            "accuracy": values["correct"] / scored if scored else None,
            "coverage": values["predicted"] / scored if scored else None,
        }
    all_scored = sum(values["scored"] for values in counts.values())
    all_correct = sum(values["correct"] for values in counts.values())
    return {
        "fields": metrics,
        "micro_field_accuracy": all_correct / all_scored if all_scored else None,
        "exact_document_accuracy": document_correct / len(rows) if rows else None,
        "documents_evaluated": len(rows),
        "unscored_schema_fields": ["customer.address", "customer.phone", "customer.email",
                                   "vendor.address", "currency", "evidence", "confidence"],
    }


def evaluate_examples(rows: list[dict]) -> list[dict]:
    """Return per-example source, normalized values, and field-level exact matches."""
    examples = []
    for row in rows:
        expected = normalize_donut_result(row["ground_truth"]).to_dict()
        predicted = row["prediction"]
        fields = {}
        for field, path in FIELD_PATHS.items():
            gold = _value(expected, path)
            guess = _value(predicted, path)
            scored = gold is not None and gold != []
            fields[field] = {
                "scored": scored,
                "expected": gold,
                "predicted": guess,
                "exact_match": _equal(field, guess, gold) if scored else None,
            }
        examples.append({
            "source": row.get("source", {}),
            "extracted_data": predicted,
            "expected_data": expected,
            "fields": fields,
        })
    return examples


def item_prf(predicted_items: list, expected_items: list) -> dict:
    """Aligned line-item precision/recall/F1.

    An item matches when its normalized description AND amount (if the label has one)
    agree; each expected item can be matched at most once (greedy, order-independent).
    """
    def key(item):
        amount = item.get("amount")
        return (_normalized(item.get("description")),
                None if amount is None else round(float(amount), 2))

    remaining = [key(item) for item in expected_items]
    matched = 0
    for item in predicted_items:
        k = key(item)
        candidates = [e for e in remaining if e[0] == k[0] and (e[1] is None or e[1] == k[1])]
        if candidates:
            remaining.remove(candidates[0])
            matched += 1
    precision = matched / len(predicted_items) if predicted_items else None
    recall = matched / len(expected_items) if expected_items else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else (0.0 if expected_items and predicted_items else None))
    return {"matched": matched, "predicted": len(predicted_items),
            "expected": len(expected_items), "precision": precision,
            "recall": recall, "f1": f1}


def aggregate_item_prf(rows: list) -> dict:
    """Micro-averaged item P/R/F1 across documents that have labelled items."""
    matched = predicted = expected = 0
    for row in rows:
        gold = normalize_donut_result(row["ground_truth"]).to_dict()["items"]
        if not gold:
            continue
        r = item_prf(row["prediction"].get("items", []), gold)
        matched += r["matched"]; predicted += r["predicted"]; expected += r["expected"]
    p = matched / predicted if predicted else None
    r_ = matched / expected if expected else None
    f1 = 2 * p * r_ / (p + r_) if p and r_ else (0.0 if expected else None)
    return {"matched": matched, "predicted": predicted, "expected": expected,
            "precision": p, "recall": r_, "f1": f1,
            "definition": "item matches if normalized description and (labelled) amount agree"}
