"""Replaceable extraction providers that all emit the canonical invoice schema.

Providers return *untrusted* raw output. `extract_validated` is the single place
where output is schema-checked, arithmetic-checked and stamped with provenance, so
every model (Donut, a VLM, a fake) is evaluated under identical rules.
No provider here calls a hosted service; a hosted one must be added explicitly and
configured through environment variables, never source code.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .schema import (arithmetic_warnings, normalize_donut_result,
                     record_from_canonical, validation_outcome)

SCHEMA_GUIDANCE = (
    "Return exactly one valid JSON object matching this schema: invoice_number, "
    "invoice_date, customer {name,address,tax_id,phone,email}, vendor "
    "{name,address,tax_id}, items [{description,quantity,unit_price,net_amount,"
    "tax_rate_percent,vat_amount,gross_amount}], subtotal, tax_amount, total_amount, "
    "currency, bank_account_iban, confidence, evidence, review_reasons. Include every "
    "visible table row in original order; keep wrapped description lines in the same "
    "item and read each printed column independently. Do not mix net unit price, net "
    "line amount, VAT amount, and gross line amount. Represent numbers as JSON numbers "
    "with decimal points. Separate vendor and customer names from their address blocks. "
    "Use null for missing or unreadable fields; never guess, fabricate rows, derive a "
    "missing observed quantity, or overwrite printed values to satisfy arithmetic. "
    "Report ambiguity and incomplete extraction in review_reasons. The vendor issues "
    "the invoice; the customer is billed. Keep IBAN separate from addresses."
)


class InvalidModelResponse(ValueError):
    """Provider output could not be parsed or normalized into the invoice schema."""


@dataclass
class RetrievalContext:
    """Auditable guidance. `hits` carry IDs; `guidance` carries format patterns only."""
    hits: list = field(default_factory=list)
    guidance: str = ""


@dataclass
class ProviderResponse:
    """Optional raw text and diagnostics without breaking dict-returning providers."""
    output: object
    raw_response: Optional[str] = None
    diagnostics: dict = field(default_factory=dict)


class ExtractionProvider(Protocol):
    name: str
    revision: str

    def extract(self, image, context: Optional[RetrievalContext] = None) -> object:
        """Return raw output: a canonical-shape dict, a JSON string, or Donut labels."""


def build_prompt(context: Optional[RetrievalContext] = None) -> str:
    parts = [SCHEMA_GUIDANCE]
    if context and context.guidance:
        parts.append(
            "Reference formatting patterns from other invoices (INTERPRETATION HINTS "
            "ONLY; digits shown as 9, letters as A). Never copy values from them; "
            "evidence in the current invoice always wins:\n" + context.guidance)
    return "\n\n".join(parts)


def parse_model_json(text: str) -> dict:
    """Extract a JSON object from model text, tolerating code fences and prose."""
    if not isinstance(text, str):
        raise InvalidModelResponse("model output is not text")
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise InvalidModelResponse("no JSON object found in model output")
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidModelResponse(f"malformed JSON from model: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidModelResponse("model JSON must be an object")
    return parsed


def extract_validated(provider, image, source: dict,
                      context: Optional[RetrievalContext] = None) -> dict:
    """Run a provider and return {record, warnings, provenance}; raises ValueError on bad output."""
    response = provider.extract(image, context)
    diagnostics = {}
    raw_response = None
    if isinstance(response, ProviderResponse):
        raw, raw_response, diagnostics = response.output, response.raw_response, response.diagnostics
    else:
        raw = response
    if isinstance(raw, str):
        raw = parse_model_json(raw)
    if not isinstance(raw, dict):
        raise InvalidModelResponse("provider output must be a JSON object")
    prompt_consumed = getattr(provider, "consumes_prompt", True)
    provenance = {
        "provider": provider.name,
        "model_revision": provider.revision,
        "prompt": build_prompt(context) if prompt_consumed else None,
        "prompt_consumed": prompt_consumed,
        "retrieval_ids": [hit["id"] for hit in (context.hits if context else [])],
        "raw_model_response": raw_response,
        "generation_diagnostics": diagnostics,
        **source,
    }
    donut_style = any(key in raw for key in ("header", "summary", "gt_parse")) \
        and "invoice_number" not in raw
    try:
        record = (normalize_donut_result(raw, provenance) if donut_style
                  else record_from_canonical(raw, provenance)).to_dict()
    except ValueError as exc:
        raise InvalidModelResponse(str(exc)) from exc
    warnings = arithmetic_warnings(record)
    validation_status, review_reasons = validation_outcome(record, warnings, diagnostics)
    provider_review_reasons = raw.get("review_reasons")
    if isinstance(provider_review_reasons, list):
        provider_review_reasons = [str(reason) for reason in provider_review_reasons if reason]
        review_reasons.extend(provider_review_reasons)
        if provider_review_reasons and validation_status == "valid_reconciled":
            validation_status = "ambiguous_review"
    record["confidence"] = _valid_confidence(raw.get("confidence"))
    evidence = raw.get("evidence")
    record["evidence"] = evidence if isinstance(evidence, (dict, list)) else None
    if record["confidence"] is None:
        review_reasons.append("Provider did not return a calibrated confidence score.")
    if record["evidence"] is None:
        review_reasons.append("Provider did not return field-level source evidence.")
    record["validation_status"] = validation_status
    record["review_reasons"] = review_reasons
    # Validation/reconciliation is not a human review decision.
    record["review_status"] = "needs_review"
    return {"record": record, "warnings": warnings, "provenance": provenance,
            "validation_status": validation_status, "review_reasons": review_reasons}


def _valid_confidence(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= 1 else None


class FakeProvider:
    """Deterministic provider for tests: replays canned outputs in order."""
    name = "fake"
    revision = "test"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def extract(self, image, context=None):
        self.calls.append(context)
        if not self.outputs:
            raise RuntimeError("FakeProvider has no more canned outputs")
        return self.outputs.pop(0)


class DonutProvider:
    """No-RAG baseline. Donut takes no text prompt, so retrieval context is ignored."""

    def __init__(self, device: str = "cpu"):
        from .donut import MODEL_ID, MODEL_REVISION, load_model
        self.name, self.revision, self.device = MODEL_ID, MODEL_REVISION, device
        self.consumes_prompt = False
        self.processor, self.model = load_model(device)

    def extract(self, image, context=None):
        from .donut import extract_image_with_diagnostics
        result = extract_image_with_diagnostics(image, self.processor, self.model, self.device)
        return ProviderResponse(result["payload"], result["raw_response"], result["diagnostics"])
