"""SQLite source ledger: extraction versions, reviews, support claims and graph changes.

Storage model
-------------
``extraction_versions``  every machine extraction ever accepted, never overwritten.
                         ``state`` is active | superseded | removed (one active per
                         document). The machine ``record`` JSON is immutable; review
                         decisions live in ``reviews``.
``reviews``              append-only review history (approved / corrected). A newer
                         review of the same extraction marks the older one superseded.
``claims``               per-document support for each node/edge, with its own
                         review status and evidence. Old claims are deactivated, not
                         deleted, so support history stays auditable.
``nodes`` / ``edges``    the materialized graph: exactly the union of active claims.
``graph_changes``        one row per graph-affecting write, computed by diffing the
                         affected node/edge rows before and after, in the same
                         transaction as the write, so it cannot drift from the graph.

All writes run in ``BEGIN IMMEDIATE`` transactions (single writer) on a WAL database.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .graph_ontology import canonical_json

SCHEMA_VERSION = 2
REVIEWED_STATUSES = frozenset({"approved", "corrected"})

_TABLES = [
    """CREATE TABLE IF NOT EXISTS extraction_versions (
        extraction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        version INTEGER,
        source_file TEXT,
        record TEXT NOT NULL,
        warnings TEXT NOT NULL DEFAULT '[]',
        validation_status TEXT,
        review_status TEXT NOT NULL DEFAULT 'needs_review'
            CHECK (review_status IN ('needs_review','approved','corrected')),
        state TEXT NOT NULL CHECK (state IN ('active','superseded','removed')),
        superseded_by INTEGER REFERENCES extraction_versions(extraction_id),
        provider TEXT,
        model_revision TEXT,
        prompt_version TEXT,
        run_id TEXT,
        sync_evidence TEXT,
        created_at TEXT NOT NULL,
        ended_at TEXT)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_active_extraction
        ON extraction_versions(document_id) WHERE state = 'active'""",
    """CREATE TABLE IF NOT EXISTS reviews (
        review_id INTEGER PRIMARY KEY AUTOINCREMENT,
        extraction_id INTEGER NOT NULL REFERENCES extraction_versions(extraction_id),
        document_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('approved','corrected')),
        reviewer TEXT,
        note TEXT,
        corrected_record TEXT,
        state TEXT NOT NULL CHECK (state IN ('active','superseded')),
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS claims (
        claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
        extraction_id INTEGER NOT NULL REFERENCES extraction_versions(extraction_id),
        document_id TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        version INTEGER,
        kind TEXT NOT NULL CHECK (kind IN ('node','edge')),
        ref_id TEXT NOT NULL,
        type TEXT NOT NULL,
        src TEXT,
        dst TEXT,
        props TEXT NOT NULL,
        match_key TEXT,
        evidence TEXT,
        review_status TEXT NOT NULL,
        active INTEGER NOT NULL CHECK (active IN (0, 1)))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_active_claim
        ON claims(document_id, kind, ref_id) WHERE active = 1""",
    "CREATE INDEX IF NOT EXISTS claims_by_ref ON claims(kind, ref_id, active)",
    "CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, type TEXT NOT NULL, props TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS edges (edge_id TEXT PRIMARY KEY, type TEXT NOT NULL,
        src TEXT NOT NULL, dst TEXT NOT NULL, props TEXT NOT NULL DEFAULT '{}')""",
    """CREATE TABLE IF NOT EXISTS graph_changes (
        change_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        document_id TEXT,
        content_hash TEXT,
        version INTEGER,
        extraction_id INTEGER,
        action TEXT NOT NULL CHECK (action IN
            ('create','update','unchanged','delete','review','rebuild','migrate')),
        nodes_added TEXT NOT NULL DEFAULT '[]',
        nodes_updated TEXT NOT NULL DEFAULT '[]',
        nodes_removed TEXT NOT NULL DEFAULT '[]',
        edges_added TEXT NOT NULL DEFAULT '[]',
        edges_updated TEXT NOT NULL DEFAULT '[]',
        edges_removed TEXT NOT NULL DEFAULT '[]',
        provider TEXT,
        model_revision TEXT,
        prompt_version TEXT,
        created_at TEXT NOT NULL)""",
]
LEGACY_RENAMES = (("extractions", "legacy_v1_extractions"), ("support", "legacy_v1_support"),
                  ("synced", "legacy_v1_synced"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def connect(path) -> sqlite3.Connection:
    """Autocommit connection; callers open explicit transactions with ``write``."""
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 30000")
    db.execute("PRAGMA foreign_keys = ON")
    if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
        db.execute("PRAGMA journal_mode = WAL")
    return db


@contextmanager
def write(db: sqlite3.Connection):
    """Single-writer transaction: everything inside commits together or not at all."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def _table_names(db) -> set:
    return {row["name"] for row in
            db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def migrate(db, build_claims_for) -> None:
    """Create or upgrade the schema. Safe to run repeatedly.

    Version 0 databases from the original store keep their data: the old
    ``extractions``/``support``/``synced`` tables are renamed ``legacy_v1_*`` (not
    dropped), each stored extraction is imported as an active, unreviewed-or-reviewed
    version, and the graph is rebuilt from the resulting claims.
    ``build_claims_for(document_id, content_hash, version, record, filename, status)``
    is the deterministic claim builder.
    """
    if db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    with write(db):
        if db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        existing = _table_names(db)
        legacy = "extractions" in existing
        for old, new in LEGACY_RENAMES:
            if old in existing and new not in existing:
                db.execute(f"ALTER TABLE {old} RENAME TO {new}")
        if "edges" in existing:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(edges)")}
            if "props" not in columns:
                db.execute("ALTER TABLE edges ADD COLUMN props TEXT NOT NULL DEFAULT '{}'")
        for statement in _TABLES:
            db.execute(statement)
        if legacy:
            _import_legacy(db, build_claims_for)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _import_legacy(db, build_claims_for) -> None:
    timestamp = now()
    rows = db.execute("SELECT document_id, content_hash, record, warnings "
                      "FROM legacy_v1_extractions ORDER BY document_id").fetchall()
    for row in rows:
        record = json.loads(row["record"])
        provenance = record.get("provenance") or {}
        status = record.get("review_status")
        status = status if status in REVIEWED_STATUSES else "needs_review"
        version = provenance.get("version")
        filename = provenance.get("source_file") or ""
        cursor = db.execute(
            "INSERT INTO extraction_versions(document_id, content_hash, version, source_file, "
            "record, warnings, validation_status, review_status, state, provider, "
            "model_revision, prompt_version, run_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'active', ?,?,?, 'migration:v1', ?)",
            (row["document_id"], row["content_hash"], version, filename, row["record"],
             row["warnings"], record.get("validation_status"), status,
             provenance.get("provider"), provenance.get("model_revision"),
             provenance.get("prompt_version"), timestamp))
        extraction_id = cursor.lastrowid
        if status in REVIEWED_STATUSES:
            db.execute("INSERT INTO reviews(extraction_id, document_id, decision, reviewer, note, "
                       "corrected_record, state, created_at) VALUES (?,?,?,?,?,NULL,'active',?)",
                       (extraction_id, row["document_id"], status, "legacy-import",
                        "Imported from schema v1; corrected values, if any, were not recorded.",
                        timestamp))
        insert_claims(db, extraction_id, row["document_id"], row["content_hash"], version,
                      build_claims_for(row["document_id"], row["content_hash"], version,
                                       record, filename, status), status)
    before = snapshot_all(db)
    materialize_all(db)
    change = diff(before, snapshot_all(db))
    if rows or any(change.values()):
        record_change(db, "migrate", change, run_id="migration:v1")


def insert_claims(db, extraction_id, document_id, content_hash, version, claims,
                  review_status) -> set:
    refs = set()
    for claim in claims:
        db.execute(
            "INSERT INTO claims(extraction_id, document_id, content_hash, version, kind, "
            "ref_id, type, src, dst, props, match_key, evidence, review_status, active) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (extraction_id, document_id, content_hash, version, claim["kind"],
             claim["ref_id"], claim["type"], claim["src"], claim["dst"],
             canonical_json(claim["props"]), claim["match_key"],
             canonical_json(claim["evidence"]), review_status))
        refs.add((claim["kind"], claim["ref_id"]))
    return refs


def deactivate_claims(db, document_id) -> set:
    refs = {(row["kind"], row["ref_id"]) for row in db.execute(
        "SELECT kind, ref_id FROM claims WHERE document_id=? AND active=1", (document_id,))}
    db.execute("UPDATE claims SET active=0 WHERE document_id=? AND active=1", (document_id,))
    return refs


# --- Materialization ---------------------------------------------------------------

_CLAIM_ORDER = "ORDER BY kind, ref_id, document_id, extraction_id, claim_id"


def compute_graph(claim_rows) -> tuple:
    """Pure union of claims -> ({node_id: (type, props)}, {edge_id: (type, src, dst, props)}).

    Rows must be ordered by (kind, ref_id, document_id, extraction_id, claim_id). A
    node shared by several documents takes its properties from the claim with the
    lowest document ID, so the result never depends on processing order; differing
    source spellings are kept in ``variants``.
    """
    grouped = {}
    for row in claim_rows:
        grouped.setdefault((row["kind"], row["ref_id"]), []).append(row)
    nodes, edges = {}, {}
    for (kind, ref_id), rows in grouped.items():
        first = rows[0]
        props = json.loads(first["props"])
        distinct = sorted({row["props"] for row in rows})
        if len(distinct) > 1:
            props = dict(props, variants=[json.loads(item) for item in distinct])
        if kind == "node":
            nodes[ref_id] = (first["type"], props)
        else:
            edges[ref_id] = (first["type"], first["src"], first["dst"], props)
    return nodes, edges


def materialize_refs(db, refs) -> None:
    """Recompute only the given node/edge rows from their active claims."""
    for kind, ref_id in sorted(refs):
        rows = db.execute(f"SELECT * FROM claims WHERE active=1 AND kind=? AND ref_id=? "
                          f"{_CLAIM_ORDER}", (kind, ref_id)).fetchall()
        nodes, edges = compute_graph(rows)
        if kind == "node":
            if ref_id in nodes:
                node_type, props = nodes[ref_id]
                db.execute("INSERT INTO nodes(node_id, type, props) VALUES (?,?,?) "
                           "ON CONFLICT(node_id) DO UPDATE SET type=excluded.type, "
                           "props=excluded.props", (ref_id, node_type, canonical_json(props)))
            else:
                db.execute("DELETE FROM nodes WHERE node_id=?", (ref_id,))
        else:
            if ref_id in edges:
                edge_type, src, dst, props = edges[ref_id]
                db.execute("INSERT INTO edges(edge_id, type, src, dst, props) VALUES (?,?,?,?,?) "
                           "ON CONFLICT(edge_id) DO UPDATE SET type=excluded.type, "
                           "src=excluded.src, dst=excluded.dst, props=excluded.props",
                           (ref_id, edge_type, src, dst, canonical_json(props)))
            else:
                db.execute("DELETE FROM edges WHERE edge_id=?", (ref_id,))


def materialize_all(db) -> None:
    """Replace the graph tables with the union of all active claims."""
    nodes, edges = compute_graph(db.execute(
        f"SELECT * FROM claims WHERE active=1 {_CLAIM_ORDER}").fetchall())
    db.execute("DELETE FROM nodes")
    db.execute("DELETE FROM edges")
    db.executemany("INSERT INTO nodes(node_id, type, props) VALUES (?,?,?)",
                   [(nid, t, canonical_json(p)) for nid, (t, p) in nodes.items()])
    db.executemany("INSERT INTO edges(edge_id, type, src, dst, props) VALUES (?,?,?,?,?)",
                   [(eid, t, s, d, canonical_json(p)) for eid, (t, s, d, p) in edges.items()])


def snapshot(db, refs) -> dict:
    """Canonical representation of the given materialized rows (absent rows omitted)."""
    result = {}
    for kind, ref_id in refs:
        if kind == "node":
            row = db.execute("SELECT type, props FROM nodes WHERE node_id=?", (ref_id,)).fetchone()
            if row:
                result[(kind, ref_id)] = canonical_json([row["type"], json.loads(row["props"])])
        else:
            row = db.execute("SELECT type, src, dst, props FROM edges WHERE edge_id=?",
                             (ref_id,)).fetchone()
            if row:
                result[(kind, ref_id)] = canonical_json(
                    [row["type"], row["src"], row["dst"], json.loads(row["props"])])
    return result


def snapshot_all(db) -> dict:
    refs = [("node", row["node_id"]) for row in db.execute("SELECT node_id FROM nodes")]
    refs += [("edge", row["edge_id"]) for row in db.execute("SELECT edge_id FROM edges")]
    return snapshot(db, refs)


def diff(before: dict, after: dict) -> dict:
    change = {f"{kind}s_{verb}": [] for kind in ("node", "edge")
              for verb in ("added", "updated", "removed")}
    for key in sorted(set(before) | set(after)):
        kind, ref_id = key
        if key not in before:
            change[f"{kind}s_added"].append(ref_id)
        elif key not in after:
            change[f"{kind}s_removed"].append(ref_id)
        elif before[key] != after[key]:
            change[f"{kind}s_updated"].append(ref_id)
    return change


def record_change(db, action, change, run_id=None, document_id=None, content_hash=None,
                  version=None, extraction_id=None, provider=None, model_revision=None,
                  prompt_version=None) -> int:
    cursor = db.execute(
        "INSERT INTO graph_changes(run_id, document_id, content_hash, version, extraction_id, "
        "action, nodes_added, nodes_updated, nodes_removed, edges_added, edges_updated, "
        "edges_removed, provider, model_revision, prompt_version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, document_id, content_hash, version, extraction_id, action,
         *(json.dumps(change[key]) for key in ("nodes_added", "nodes_updated", "nodes_removed",
                                               "edges_added", "edges_updated", "edges_removed")),
         provider, model_revision, prompt_version, now()))
    return cursor.lastrowid
