"""Conservative, rule-based extraction quality score for UI triage.

This is an explainable proxy, not a statistically calibrated probability. It combines
core invoice-field coverage, quoted field evidence, and deterministic validation.
"""

CORE_FIELDS = (
    "invoice_number", "invoice_date", "vendor.name", "customer.name", "total_amount",
)


def estimate_extraction_confidence(record: dict) -> float:
    """Return a 0..1 quality score based on observed fields, evidence, and checks."""
    record = record if isinstance(record, dict) else {}

    def value_for(path):
        current = record
        for part in path.split("."):
            current = current.get(part) if isinstance(current, dict) else None
        return current

    present = [path for path in CORE_FIELDS if value_for(path) not in (None, "", [])]
    completeness = len(present) / len(CORE_FIELDS)
    field_evidence = record.get("field_evidence") or {}
    supported = 0
    for path in present:
        item = field_evidence.get(path) or {}
        match = item.get("match") or {}
        source = match.get("source") or {}
        if (item.get("quote") and source.get("status") in
                {"exact", "normalized", "ocr_folded", "fuzzy", "lines"}
                and float(source.get("score") or 0) >= 0.86):
            supported += 1
    evidence_coverage = supported / len(present) if present else 0.0

    status = record.get("validation_status")
    validation = {
        "valid_reconciled": 1.0,
        "ambiguous_review": 0.55,
        "incomplete_extraction": 0.25,
        "arithmetic_mismatch": 0.0,
        "invalid_response": 0.0,
    }.get(status, 0.2)
    return round(0.45 * completeness + 0.35 * evidence_coverage + 0.20 * validation, 4)
