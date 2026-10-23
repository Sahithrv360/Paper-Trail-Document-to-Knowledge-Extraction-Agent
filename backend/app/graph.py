"""SQLite knowledge-graph store with per-document support and idempotent sync.

Nodes/edges exist only while at least one (document_id, content_hash) supports them.
Re-syncing the same version is a no-op; a new version replaces only that document's
support; deleting a document never removes entities still supported by another.
Entities merge only on exact normalized keys (never fuzzy name similarity).
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, type TEXT NOT NULL, props TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edges (edge_id TEXT PRIMARY KEY, type TEXT NOT NULL,
    src TEXT NOT NULL, dst TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS support (document_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    kind TEXT NOT NULL, ref_id TEXT NOT NULL, evidence TEXT,
    PRIMARY KEY (document_id, kind, ref_id));
CREATE TABLE IF NOT EXISTS synced (document_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS extractions (document_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
    record TEXT NOT NULL, warnings TEXT NOT NULL);
"""


def _key(*parts) -> str:
    text = "|".join(" ".join(str(p).split()).casefold() for p in parts)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class GraphStore:
    def __init__(self, path):
        self.path = Path(path)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _build(document_id, content_hash, record, filename):
        nodes, edges = {}, []

        def node(kind, key, **props):
            nid = f"{kind}:{key}"
            nodes[nid] = (kind, props)
            return nid

        doc = node("Document", document_id, document_id=document_id,
                   content_hash=content_hash, filename=filename)
        inv = node("Invoice", f"{document_id}:{_key(record.get('invoice_number'))}",
                   invoice_number=record.get("invoice_number"),
                   invoice_date=record.get("invoice_date"),
                   subtotal=record.get("subtotal"), tax_amount=record.get("tax_amount"),
                   total_amount=record.get("total_amount"), currency=record.get("currency"),
                   review_status=record.get("review_status"))
        edges.append(("SUPPORTS", doc, inv))
        for role, kind, edge in (("vendor", "Vendor", "ISSUED"), ("customer", "Customer", "BILLED_TO")):
            party = record.get(role) or {}
            if not party.get("name"):
                continue  # unknown party: no node rather than an invented one
            pid = node(kind, _key(party["name"], party.get("tax_id") or ""), **party)
            edges.append((edge, pid, inv) if role == "vendor" else (edge, inv, pid))
        for index, item in enumerate(record.get("items") or []):
            lid = node("LineItem", f"{document_id}:{index}", position=index, **item)
            edges.append(("CONTAINS", inv, lid))
        return nodes, edges

    def sync(self, document_id: str, content_hash: str, record: dict,
             filename: str = "", evidence: dict = None, warnings: list = None,
             allow_reviewed_replace: bool = False,
             allow_same_version_replace: bool = False) -> bool:
        """Apply a validated record. Returns False if this exact version was already synced."""
        with self._db() as db:
            row = db.execute("SELECT content_hash FROM synced WHERE document_id=?",
                             (document_id,)).fetchone()
            if row and row["content_hash"] == content_hash and not allow_same_version_replace:
                return False
            previous = db.execute("SELECT record FROM extractions WHERE document_id=?",
                                  (document_id,)).fetchone()
            if previous and not allow_reviewed_replace:
                previous_record = json.loads(previous["record"])
                if previous_record.get("review_status") in {"approved", "corrected"}:
                    raise ReviewedExtractionConflict(
                        f"Document {document_id} has a reviewed extraction; explicitly "
                        "allow replacement after review."
                    )
            self._drop_support(db, document_id)
            nodes, edges = self._build(document_id, content_hash, record, filename)
            ev = json.dumps(evidence or record.get("provenance") or {}, sort_keys=True)
            for nid, (kind, props) in nodes.items():
                db.execute("INSERT OR REPLACE INTO nodes VALUES (?,?,?)",
                           (nid, kind, json.dumps(props, sort_keys=True)))
                db.execute("INSERT OR REPLACE INTO support VALUES (?,?,?,?,?)",
                           (document_id, content_hash, "node", nid, ev))
            for kind, src, dst in edges:
                eid = f"{src}-{kind}->{dst}"
                db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?)", (eid, kind, src, dst))
                db.execute("INSERT OR REPLACE INTO support VALUES (?,?,?,?,?)",
                           (document_id, content_hash, "edge", eid, ev))
            db.execute("INSERT OR REPLACE INTO extractions VALUES (?,?,?,?)",
                       (document_id, content_hash, json.dumps(record, sort_keys=True),
                        json.dumps(warnings or [])))
            db.execute("INSERT OR REPLACE INTO synced VALUES (?,?)", (document_id, content_hash))
        return True

    def remove_document(self, document_id: str) -> None:
        with self._db() as db:
            self._drop_support(db, document_id)
            db.execute("DELETE FROM synced WHERE document_id=?", (document_id,))
            db.execute("DELETE FROM extractions WHERE document_id=?", (document_id,))

    def get_extraction(self, document_id: str):
        """The last valid extraction for a document (record + warnings), or None."""
        with self._db() as db:
            row = db.execute("SELECT * FROM extractions WHERE document_id=?",
                             (document_id,)).fetchone()
        if row is None:
            return None
        record = json.loads(row["record"])
        # Non-destructive read compatibility: old JSON rows retain their original
        # `amount` untouched; the new fields are exposed as unknown until re-extracted.
        for item in record.get("items", []):
            if not isinstance(item, dict):
                continue
            for key in ("net_amount", "vat_amount", "gross_amount"):
                item.setdefault(key, None)
            if item.get("amount") is not None:
                item.setdefault("amount_semantics", "unknown")
        record.setdefault("validation_status", "incomplete_extraction")
        record.setdefault("review_reasons", ["Legacy extraction has no explicit validation outcome."])
        return {"document_id": document_id, "content_hash": row["content_hash"],
                "record": record, "warnings": json.loads(row["warnings"])}

    def mark_reviewed(self, document_id: str, status: str) -> None:
        """Persist a review decision; reprocessing then requires explicit replacement."""
        if status not in {"approved", "corrected"}:
            raise ValueError("review status must be 'approved' or 'corrected'")
        with self._db() as db:
            row = db.execute("SELECT record FROM extractions WHERE document_id=?",
                             (document_id,)).fetchone()
            if row is None:
                raise KeyError(document_id)
            record = json.loads(row["record"])
            record["review_status"] = status
            db.execute("UPDATE extractions SET record=? WHERE document_id=?",
                       (json.dumps(record, sort_keys=True), document_id))

    @staticmethod
    def _drop_support(db, document_id):
        db.execute("DELETE FROM support WHERE document_id=?", (document_id,))
        db.execute("DELETE FROM edges WHERE edge_id NOT IN "
                   "(SELECT ref_id FROM support WHERE kind='edge')")
        db.execute("DELETE FROM nodes WHERE node_id NOT IN "
                   "(SELECT ref_id FROM support WHERE kind='node')")

    def export(self) -> dict:
        """Deterministic JSON nodes/edges with supporting documents, for a UI to consume."""
        with self._db() as db:
            sup = {}
            for r in db.execute("SELECT document_id, content_hash, kind, ref_id FROM support"):
                sup.setdefault((r["kind"], r["ref_id"]), []).append(
                    {"document_id": r["document_id"], "content_hash": r["content_hash"]})
            nodes = []
            for r in db.execute("SELECT * FROM nodes ORDER BY node_id"):
                props = json.loads(r["props"])
                if r["type"] == "LineItem":
                    for key in ("net_amount", "vat_amount", "gross_amount"):
                        props.setdefault(key, None)
                    if props.get("amount") is not None:
                        props.setdefault("amount_semantics", "unknown")
                nodes.append({"id": r["node_id"], "type": r["type"], "props": props,
                              "support": sorted(sup.get(("node", r["node_id"]), []), key=str)})
            edges = [{"id": r["edge_id"], "type": r["type"], "source": r["src"], "target": r["dst"],
                      "support": sorted(sup.get(("edge", r["edge_id"]), []), key=str)}
                     for r in db.execute("SELECT * FROM edges ORDER BY edge_id")]
        return {"nodes": nodes, "edges": edges}


class ReviewedExtractionConflict(RuntimeError):
    """Prevents an unattended run from overwriting a human-reviewed record."""
