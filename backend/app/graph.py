"""SQLite knowledge graph materialized from a per-document source ledger.

Public API (compatible with the original store, extended for Milestone 1):

* ``sync`` applies one validated extraction for a document version. Re-syncing the
  same version is a no-op; a new version supersedes (never deletes) the previous
  extraction and replaces only that document's support.
* ``remove_document`` withdraws one document's support; shared entities survive while
  another document supports them.
* ``apply_review`` records a human decision (append-only) and re-derives that
  document's support with the reviewed status in the same transaction.
* ``export(reviewed_only=...)`` returns typed nodes/edges with per-source support and
  review state; ``changes`` returns the graph-change ledger.

Invariant: the ``nodes``/``edges`` tables always equal the union of the active claims
of all non-removed documents (``rebuild_graph`` recomputes and reports any drift).
Entities merge only on exact match keys; there is no fuzzy merging.
"""

import json
from contextlib import contextmanager
from pathlib import Path

from .extraction.evidence import raw_sources, record_values
from .graph_ontology import SHARED_NODE_TYPES, build_claims, canonical_json
from .extraction.matching import name_key, tax_id_key
from .ledger import (REVIEWED_STATUSES, compute_graph, connect, deactivate_claims, diff,
                     insert_claims, materialize_all, materialize_refs, migrate, now,
                     record_change, snapshot, snapshot_all, write)


class ReviewedExtractionConflict(RuntimeError):
    """Prevents an unattended run from superseding a human-reviewed record."""


class GraphStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            migrate(db, self._claims_for)

    @contextmanager
    def _db(self):
        db = connect(self.path)
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _claims_for(document_id, content_hash, version, record, filename, review_status):
        return build_claims(document_id, content_hash, version, record, filename, review_status)

    @staticmethod
    def _active(db, document_id):
        return db.execute("SELECT * FROM extraction_versions WHERE document_id=? AND "
                          "state='active'", (document_id,)).fetchone()

    def _apply_claims(self, db, extraction_id, document_id, content_hash, version, record,
                      filename, review_status) -> dict:
        """Swap one document's active claims and re-materialize only what they touch."""
        old_refs = deactivate_claims(db, document_id)
        claims = self._claims_for(document_id, content_hash, version, record, filename,
                                  review_status)
        refs = old_refs | insert_claims(db, extraction_id, document_id, content_hash, version,
                                        claims, review_status)
        before = snapshot(db, refs)
        self._materialize_refs(db, refs)
        return diff(before, snapshot(db, refs))

    def _materialize_refs(self, db, refs) -> None:  # separate method so tests can inject faults
        materialize_refs(db, refs)

    # --- Writes ------------------------------------------------------------------------

    def sync(self, document_id: str, content_hash: str, record: dict,
             filename: str = "", evidence: dict = None, warnings: list = None,
             allow_reviewed_replace: bool = False,
             allow_same_version_replace: bool = False, version=None,
             run_id: str = None) -> bool:
        """Apply a validated record. Returns False when nothing changed.

        The stored machine extraction is always ``needs_review``; only ``apply_review``
        can record an approval or correction.
        """
        provenance = record.get("provenance") or {}
        version = version if version is not None else provenance.get("version")
        machine = dict(record, review_status="needs_review")
        record_json = canonical_json(machine)
        with self._db() as db, write(db):
            active = self._active(db, document_id)
            if active and active["content_hash"] == content_hash and not allow_same_version_replace:
                return False
            if active and active["review_status"] in REVIEWED_STATUSES and not allow_reviewed_replace:
                raise ReviewedExtractionConflict(
                    f"Document {document_id} has a reviewed extraction; explicitly "
                    "allow replacement after review.")
            origin = {"provider": provenance.get("provider"),
                      "model_revision": provenance.get("model_revision"),
                      "prompt_version": provenance.get("prompt_version")}
            if (active and active["content_hash"] == content_hash
                    and active["record"] == record_json):
                # Explicit same-version rerun produced an identical extraction.
                record_change(db, "unchanged", diff({}, {}), run_id=run_id,
                              document_id=document_id, content_hash=content_hash,
                              version=version, extraction_id=active["extraction_id"], **origin)
                return False
            timestamp = now()
            cursor = None
            if active:
                db.execute("UPDATE extraction_versions SET state='superseded', ended_at=? "
                           "WHERE extraction_id=?", (timestamp, active["extraction_id"]))
            cursor = db.execute(
                "INSERT INTO extraction_versions(document_id, content_hash, version, source_file, "
                "record, warnings, validation_status, review_status, state, provider, "
                "model_revision, prompt_version, run_id, sync_evidence, created_at) "
                "VALUES (?,?,?,?,?,?,?, 'needs_review', 'active', ?,?,?,?,?,?)",
                (document_id, content_hash, version, filename, record_json,
                 json.dumps(warnings or []), record.get("validation_status"),
                 origin["provider"], origin["model_revision"], origin["prompt_version"],
                 run_id, canonical_json(evidence) if evidence is not None else None, timestamp))
            extraction_id = cursor.lastrowid
            if active:
                db.execute("UPDATE extraction_versions SET superseded_by=? WHERE extraction_id=?",
                           (extraction_id, active["extraction_id"]))
            change = self._apply_claims(db, extraction_id, document_id, content_hash, version,
                                        machine, filename, "needs_review")
            record_change(db, "update" if active else "create", change, run_id=run_id,
                          document_id=document_id, content_hash=content_hash, version=version,
                          extraction_id=extraction_id, **origin)
        return True

    def remove_document(self, document_id: str, run_id: str = None) -> bool:
        """Withdraw this document's support. Its extraction history is retained."""
        with self._db() as db, write(db):
            active = self._active(db, document_id)
            if active is None:
                return False
            db.execute("UPDATE extraction_versions SET state='removed', ended_at=? "
                       "WHERE extraction_id=?", (now(), active["extraction_id"]))
            refs = deactivate_claims(db, document_id)
            before = snapshot(db, refs)
            self._materialize_refs(db, refs)
            record_change(db, "delete", diff(before, snapshot(db, refs)), run_id=run_id,
                          document_id=document_id, content_hash=active["content_hash"],
                          version=active["version"], extraction_id=active["extraction_id"],
                          provider=active["provider"], model_revision=active["model_revision"],
                          prompt_version=active["prompt_version"])
        return True

    def apply_review(self, document_id: str, decision: str, corrected_record: dict = None,
                     reviewer: str = None, note: str = None, run_id: str = None) -> dict:
        """Record a human review and update that document's graph support atomically.

        ``approved`` accepts the current effective record (the machine extraction, or
        the active correction if an earlier review corrected it). ``corrected`` stores
        the reviewer's full corrected record beside (never over) the machine extraction.
        A later review of the same extraction supersedes, but does not erase, this one.
        """
        if decision not in REVIEWED_STATUSES:
            raise ValueError("review decision must be 'approved' or 'corrected'")
        if decision == "approved" and corrected_record is not None:
            raise ValueError("an approval cannot carry corrections; use 'corrected'")
        if decision == "corrected" and not isinstance(corrected_record, dict):
            raise ValueError("a correction requires the full corrected record")
        with self._db() as db, write(db):
            active = self._active(db, document_id)
            if active is None:
                raise KeyError(document_id)
            machine = json.loads(active["record"])
            if decision == "approved":
                # Approval accepts the *current* effective record: a correction made by an
                # earlier review of this extraction is carried forward, not discarded.
                prior = db.execute("SELECT corrected_record FROM reviews WHERE extraction_id=? "
                                   "AND state='active' ORDER BY review_id DESC LIMIT 1",
                                   (active["extraction_id"],)).fetchone()
                carried = prior["corrected_record"] if prior else None
                effective = json.loads(carried) if carried else machine
                stored_correction = carried
            else:
                effective = self._validated_correction(machine, corrected_record)
                stored_correction = canonical_json(effective)
            timestamp = now()
            db.execute("UPDATE reviews SET state='superseded' WHERE extraction_id=? AND "
                       "state='active'", (active["extraction_id"],))
            cursor = db.execute(
                "INSERT INTO reviews(extraction_id, document_id, decision, reviewer, note, "
                "corrected_record, state, created_at) VALUES (?,?,?,?,?,?,'active',?)",
                (active["extraction_id"], document_id, decision, reviewer, note,
                 stored_correction, timestamp))
            db.execute("UPDATE extraction_versions SET review_status=? WHERE extraction_id=?",
                       (decision, active["extraction_id"]))
            change = self._apply_claims(db, active["extraction_id"], document_id,
                                        active["content_hash"], active["version"],
                                        dict(effective, review_status=decision),
                                        active["source_file"] or "", decision)
            record_change(db, "review", change, run_id=run_id, document_id=document_id,
                          content_hash=active["content_hash"], version=active["version"],
                          extraction_id=active["extraction_id"], provider=active["provider"],
                          model_revision=active["model_revision"],
                          prompt_version=active["prompt_version"])
        return {"review_id": cursor.lastrowid, "extraction_id": active["extraction_id"],
                "change": change}

    def mark_reviewed(self, document_id: str, status: str) -> None:
        """Compatibility wrapper. Prefer ``apply_review`` with explicit corrections."""
        if status not in REVIEWED_STATUSES:
            raise ValueError("review status must be 'approved' or 'corrected'")
        if status == "approved":
            self.apply_review(document_id, "approved")
            return
        current = self.get_extraction(document_id)
        if current is None:
            raise KeyError(document_id)
        self.apply_review(document_id, "corrected", current["effective_record"],
                          note="mark_reviewed('corrected'): corrected values not supplied")

    @staticmethod
    def _validated_correction(machine: dict, corrected: dict) -> dict:
        """Validate a reviewer's record; keep machine evidence only for unchanged values."""
        from .extraction.schema import record_from_canonical
        from .extraction.validation import status_from_findings, structured_findings

        record = record_from_canonical(corrected, machine.get("provenance") or {}).to_dict()
        old_values, new_values = record_values(machine), record_values(record)
        old_evidence = machine.get("field_evidence") or {}
        typed = raw_sources(corrected, donut_style=False)
        evidence = {}
        for path, value in new_values.items():
            if value is None:
                continue
            if path in old_evidence and old_values.get(path) == value:
                evidence[path] = old_evidence[path]
            else:
                raw = typed.get(path)
                evidence[path] = {"raw_text": raw if isinstance(raw, str) else None,
                                  "raw_type": None if raw is None else type(raw).__name__,
                                  "value": value, "page": None, "quote": None, "bbox": None,
                                  "method": "human", "match": {}}
        findings = structured_findings(record)
        record.update(field_evidence=evidence, findings=findings,
                      validation_status=status_from_findings(findings),
                      review_reasons=[item["message"] for item in findings],
                      provenance=dict(machine.get("provenance") or {}, corrected_by_review=True))
        return record

    def rebuild_graph(self, run_id: str = None) -> dict:
        """Recompute the whole graph from active claims; returns (and logs) any drift."""
        with self._db() as db, write(db):
            before = snapshot_all(db)
            materialize_all(db)
            change = diff(before, snapshot_all(db))
            if any(change.values()):
                record_change(db, "rebuild", change, run_id=run_id)
        return change

    # --- Reads -------------------------------------------------------------------------

    def get_extraction(self, document_id: str):
        """The active extraction (machine record + latest review), or None."""
        with self._db() as db:
            row = self._active(db, document_id)
            if row is None:
                return None
            review = db.execute("SELECT * FROM reviews WHERE extraction_id=? AND state='active' "
                                "ORDER BY review_id DESC LIMIT 1",
                                (row["extraction_id"],)).fetchone()
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
        record["review_status"] = row["review_status"]
        review = _review_dict(review)
        effective = (review["corrected_record"] if review and review["corrected_record"]
                     else record)
        return {"document_id": document_id, "content_hash": row["content_hash"],
                "record": record, "warnings": json.loads(row["warnings"]),
                "extraction_id": row["extraction_id"], "version": row["version"],
                "review_status": row["review_status"],
                "validation_status": row["validation_status"],
                "review": review, "effective_record": effective}

    def extraction_history(self, document_id: str) -> list:
        """Every extraction version for a document, oldest first, with its reviews."""
        with self._db() as db:
            versions = db.execute("SELECT * FROM extraction_versions WHERE document_id=? "
                                  "ORDER BY extraction_id", (document_id,)).fetchall()
            reviews = db.execute("SELECT * FROM reviews WHERE document_id=? ORDER BY review_id",
                                 (document_id,)).fetchall()
        history = []
        for row in versions:
            item = {key: row[key] for key in row.keys() if key not in ("record", "sync_evidence")}
            item["record"] = json.loads(row["record"])
            item["reviews"] = [_review_dict(review) for review in reviews
                               if review["extraction_id"] == row["extraction_id"]]
            history.append(item)
        return history

    def synced_documents(self) -> list:
        """Every document known to the graph: latest hash/version, state, review, last sync."""
        with self._db() as db:
            rows = db.execute(
                "SELECT e.document_id, e.extraction_id, e.content_hash, e.version, e.source_file, "
                "e.state, e.review_status, e.validation_status, e.provider, e.model_revision, "
                "(SELECT MAX(created_at) FROM graph_changes c WHERE c.document_id = e.document_id) "
                "AS last_synced_at "
                "FROM extraction_versions e WHERE e.extraction_id = (SELECT MAX(extraction_id) "
                "FROM extraction_versions x WHERE x.document_id = e.document_id) "
                "ORDER BY e.document_id").fetchall()
        return [dict(row) for row in rows]

    def changes(self, document_id: str = None) -> list:
        query = "SELECT * FROM graph_changes"
        args = ()
        if document_id is not None:
            query, args = query + " WHERE document_id=?", (document_id,)
        with self._db() as db:
            rows = db.execute(query + " ORDER BY change_id", args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("nodes_added", "nodes_updated", "nodes_removed",
                        "edges_added", "edges_updated", "edges_removed"):
                item[key] = json.loads(item[key])
            result.append(item)
        return result

    def export(self, reviewed_only: bool = False) -> dict:
        """Deterministic JSON nodes/edges with per-source support and review state.

        ``reviewed_only`` builds the graph from reviewed support alone, so entity merges
        and facts asserted only by unreviewed extractions are excluded.
        """
        with self._db() as db:
            claim_rows = db.execute(
                "SELECT * FROM claims WHERE active=1 "
                "ORDER BY kind, ref_id, document_id, extraction_id, claim_id").fetchall()
            if reviewed_only:
                claim_rows = [row for row in claim_rows
                              if row["review_status"] in REVIEWED_STATUSES]
                nodes, edges = compute_graph(claim_rows)
            else:
                nodes = {row["node_id"]: (row["type"], json.loads(row["props"]))
                         for row in db.execute("SELECT * FROM nodes")}
                edges = {row["edge_id"]: (row["type"], row["src"], row["dst"],
                                          json.loads(row["props"]))
                         for row in db.execute("SELECT * FROM edges")}
        support = {}
        for row in claim_rows:
            support.setdefault((row["kind"], row["ref_id"]), []).append(
                {"document_id": row["document_id"], "content_hash": row["content_hash"],
                 "version": row["version"], "extraction_id": row["extraction_id"],
                 "review_status": row["review_status"]})
        node_list = []
        for nid in sorted(nodes):
            node_type, props = nodes[nid]
            if node_type == "LineItem":
                for key in ("net_amount", "vat_amount", "gross_amount"):
                    props.setdefault(key, None)
                if props.get("amount") is not None:
                    props.setdefault("amount_semantics", "unknown")
            node_list.append({"id": nid, "type": node_type, "props": props,
                              "support": sorted(support.get(("node", nid), []), key=str)})
        edge_list = [{"id": eid, "type": edges[eid][0], "source": edges[eid][1],
                      "target": edges[eid][2], "props": edges[eid][3],
                      "support": sorted(support.get(("edge", eid), []), key=str)}
                     for eid in sorted(edges)]
        return {"nodes": node_list, "edges": edge_list}

    def entity_suggestions(self) -> list:
        """Possible same-entity pairs for a reviewer. Never merged automatically."""
        groups = {}
        for node in self.export()["nodes"]:
            if node["type"] not in SHARED_NODE_TYPES:
                continue
            props = node["props"]
            for reason, key in (("same_name_different_tax_id", name_key(props.get("name"))),
                                ("same_tax_id_different_name", tax_id_key(props.get("tax_id")))):
                if key:
                    groups.setdefault((reason, node["type"], key), []).append(node["id"])
        return [{"reason": reason, "type": node_type, "node_ids": sorted(ids)}
                for (reason, node_type, _key), ids in sorted(groups.items()) if len(ids) > 1]

    def duplicate_content_groups(self) -> list:
        """Active documents whose bytes are identical (distinct paths stay distinct)."""
        with self._db() as db:
            rows = db.execute(
                "SELECT content_hash, document_id, source_file FROM extraction_versions "
                "WHERE state='active' AND content_hash IN (SELECT content_hash FROM "
                "extraction_versions WHERE state='active' GROUP BY content_hash "
                "HAVING COUNT(*) > 1) ORDER BY content_hash, document_id").fetchall()
        groups = {}
        for row in rows:
            groups.setdefault(row["content_hash"], []).append(
                {"document_id": row["document_id"], "source_file": row["source_file"]})
        return [{"content_hash": key, "documents": value} for key, value in groups.items()]


def _review_dict(row):
    if row is None:
        return None
    review = dict(row)
    review["corrected_record"] = (json.loads(review["corrected_record"])
                                  if review["corrected_record"] else None)
    return review
