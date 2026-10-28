"""Allowed graph node/edge types, stable IDs, and the deterministic claim builder.

A *claim* is one document version's assertion that a node or edge exists, with the
properties, match key and evidence that document supports. The graph is the
materialized union of all active claims (see ``ledger.py``). This module is pure: it
reads a validated record and returns claims; it never touches storage, and nothing a
model emits is executed as a query.
"""

import hashlib
import json

from .extraction.evidence import item_path
from .extraction.matching import party_match_key
from .extraction.validation import INVOICE_LIKE_TYPES

ONTOLOGY_VERSION = "papertrail-ontology-1"
NODE_TYPES = frozenset({"Document", "Invoice", "Vendor", "Customer", "LineItem",
                        "ItemDescription", "ProductCategory"})
# Edge type -> (source node type, target node type). Direction is part of the type.
EDGE_TYPES = {
    "SUPPORTS": ("Document", "Invoice"),
    "ISSUED": ("Vendor", "Invoice"),
    "BILLED_TO": ("Invoice", "Customer"),
    "CONTAINS": ("Invoice", "LineItem"),
    # A shared exact-description hub connects matching line items across invoices.
    "MATCHES_DESCRIPTION": ("LineItem", "ItemDescription"),
    # Derived only in the Neo4j projection from shared, exact graph facts.
    "SAME_VENDOR_AS": ("Invoice", "Invoice"),
    "SAME_CUSTOMER_AS": ("Invoice", "Invoice"),
    "SAME_ITEM_DESCRIPTION_AS": ("Invoice", "Invoice"),
    # Defined for a later, evidence-backed classifier; the builder never emits it.
    "CLASSIFIED_AS": ("LineItem", "ProductCategory"),
}
SHARED_NODE_TYPES = frozenset({"Vendor", "Customer"})  # may be supported by many documents
PARTY_FIELDS = {"vendor": ("name", "address", "tax_id"),
                "customer": ("name", "address", "tax_id", "phone", "email")}
INVOICE_FIELDS = ("invoice_number", "invoice_date", "subtotal", "tax_amount",
                  "total_amount", "currency")


class OntologyError(ValueError):
    """A claim violates the allowed node/edge types or edge direction."""


def node_type_of(node_id: str) -> str:
    return node_id.split(":", 1)[0]


def node_id(node_type: str, key: str) -> str:
    if node_type not in NODE_TYPES:
        raise OntologyError(f"unknown node type {node_type!r}")
    return f"{node_type}:{key}"


def edge_id(edge_type: str, src: str, dst: str) -> str:
    return f"{src}-{edge_type}->{dst}"


def stable_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def check_edge(edge_type: str, src: str, dst: str) -> None:
    if edge_type not in EDGE_TYPES:
        raise OntologyError(f"unknown edge type {edge_type!r}")
    expected = EDGE_TYPES[edge_type]
    actual = (node_type_of(src), node_type_of(dst))
    if actual != expected:
        raise OntologyError(f"{edge_type} must connect {expected[0]} -> {expected[1]}, "
                            f"not {actual[0]} -> {actual[1]}")


def _evidence_for(record: dict, prefixes) -> dict:
    evidence = record.get("field_evidence") or {}
    return {path: entry for path, entry in evidence.items()
            if any(path == prefix or path.startswith(prefix) for prefix in prefixes)}


def build_claims(document_id: str, content_hash: str, version, record: dict,
                 filename: str = "", review_status: str = "needs_review") -> list:
    """Return the node/edge claims one document version supports, in a stable order."""
    claims = {}
    provenance = record.get("provenance") or {}
    origin = {"provider": provenance.get("provider"),
              "model_revision": provenance.get("model_revision"),
              "prompt_version": provenance.get("prompt_version")}

    def node(node_type, key, props, evidence=None, match_key=None):
        nid = node_id(node_type, key)
        claims[("node", nid)] = {"kind": "node", "ref_id": nid, "type": node_type,
                                 "src": None, "dst": None, "props": props,
                                 "match_key": match_key,
                                 "evidence": {"fields": evidence or {}, "origin": origin}}
        return nid

    def edge(edge_type, src, dst, props=None):
        check_edge(edge_type, src, dst)
        eid = edge_id(edge_type, src, dst)
        claims[("edge", eid)] = {"kind": "edge", "ref_id": eid, "type": edge_type,
                                 "src": src, "dst": dst, "props": props or {},
                                 "match_key": None, "evidence": {"origin": origin}}
        return eid

    doc_type = record.get("doc_type")
    doc = node("Document", document_id, {
        "document_id": document_id, "content_hash": content_hash, "filename": filename,
        "version": version, "doc_type": doc_type})
    if doc_type is not None and doc_type not in INVOICE_LIKE_TYPES:
        # Retained for review, but no invoice facts are invented for other documents.
        return [claims[key] for key in sorted(claims)]

    invoice = node("Invoice", document_id, {
        **{name: record.get(name) for name in INVOICE_FIELDS},
        "doc_type": doc_type, "review_status": review_status,
        "validation_status": record.get("validation_status")},
        evidence=_evidence_for(record, INVOICE_FIELDS + ("bank_account_iban",)))
    edge("SUPPORTS", doc, invoice, {"version": version, "content_hash": content_hash,
                                    "review_status": review_status})
    for role, node_type, edge_type in (("vendor", "Vendor", "ISSUED"),
                                       ("customer", "Customer", "BILLED_TO")):
        party = record.get(role) or {}
        key = party_match_key(party.get("name"), party.get("tax_id"))
        if key is None:
            continue  # unknown party: no node rather than an invented one
        props = {name: party.get(name) for name in PARTY_FIELDS[role]}
        props["match_key"] = key
        pid = node(node_type, stable_key(key), props,
                   evidence=_evidence_for(record, (f"{role}.",)), match_key=key)
        if role == "vendor":
            edge(edge_type, pid, invoice)
        else:
            edge(edge_type, invoice, pid)
    for index, item in enumerate(record.get("items") or []):
        lid = node("LineItem", f"{document_id}:{index}",
                   {"position": index, "row_index": index, **item},
                   evidence=_evidence_for(record, (item_path(index, ""),)))
        edge("CONTAINS", invoice, lid, {"row_index": index})
    return [claims[key] for key in sorted(claims)]


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)
