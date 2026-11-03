"""Reset and seed the isolated presenter graph from fictional invoice records."""

import argparse
import hashlib
import json
from pathlib import Path

from .config import GRAPH_PATH
from .graph import GraphStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESENTER_DATA = PROJECT_ROOT / "presentation_data" / "synthetic_invoices"
DEMO_RUNTIME = PROJECT_ROOT / "presentation_data" / "runtime"
DEMO_GRAPH_PATH = DEMO_RUNTIME / "paper_trail_graph.sqlite3"
UNREVIEWED_DEMO_ID = "presentation-demo-2026-010"


def load_demo_records(data_dir=PRESENTER_DATA):
    """Load and validate the structured fictional invoice records."""
    data_dir = Path(data_dir)
    files = sorted(data_dir.glob("*.json"))
    if not files:
        raise ValueError(f"No presenter invoice JSON files found in {data_dir}")
    records, document_ids = [], set()
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        document_id, record = payload.get("document_id"), payload.get("record")
        if payload.get("synthetic") is not True or not document_id or not isinstance(record, dict):
            raise ValueError(f"{path.name} must be a synthetic invoice record")
        if document_id in document_ids:
            raise ValueError(f"Duplicate presenter document_id: {document_id}")
        if not record.get("invoice_number"):
            raise ValueError(f"{path.name} is missing record.invoice_number")
        document_ids.add(document_id)
        content = path.read_bytes()
        records.append({"document_id": document_id, "source_file": path.name,
                        "path": path, "content_hash": hashlib.sha256(content).hexdigest(),
                        "record": record})
    return records


def reset_sqlite(path=DEMO_GRAPH_PATH):
    """Reset one explicitly selected database file and its SQLite sidecars."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        candidate.unlink(missing_ok=True)
    return GraphStore(path)


def seed_demo_graph(graph, records):
    """Load records and approve all except the intentionally unreviewed example."""
    approved = 0
    for item in records:
        record = dict(item["record"])
        record["provenance"] = {
            "provider": "synthetic-demo", "model_revision": "presenter-json-v1",
            "prompt_version": "none", "version": 1,
        }
        graph.sync(item["document_id"], item["content_hash"], record,
                   filename=item["source_file"], version=1,
                   run_id="presenter-demo-reset")
        if item["document_id"] != UNREVIEWED_DEMO_ID:
            graph.apply_review(item["document_id"], "approved", reviewer="demo-seed")
            approved += 1
    return {"documents": len(records), "approved": approved,
            "needs_review": len(records) - approved}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Reset the isolated SQLite presenter graph and load ten fictional invoices.")
    parser.add_argument("--sync-neo4j", action="store_true",
                        help="also replace PaperTrail-owned nodes and edges in configured Neo4j")
    args = parser.parse_args(argv)

    # The demo DB is always separate from GRAPH_PATH and DATABASE_PATH; no path override is exposed.
    if DEMO_GRAPH_PATH.resolve() == Path(GRAPH_PATH).resolve():
        parser.error("presenter database resolves to the live graph database; refusing reset")
    records = load_demo_records()
    graph = reset_sqlite()
    result = seed_demo_graph(graph, records)
    result.update({"sqlite_graph": str(DEMO_GRAPH_PATH), "neo4j_synced": False})

    if args.sync_neo4j:
        from .neo4j_sync import count_projected_relationships, create_driver, sync_graph_export

        driver = create_driver()
        try:
            driver.verify_connectivity()
            synced = sync_graph_export(graph.export(), driver)
            actual = count_projected_relationships(driver)
            if actual != synced["relationships_by_type"]:
                raise RuntimeError("Neo4j relationship counts did not match the presenter snapshot")
            result["neo4j_synced"] = True
            result["neo4j_relationships"] = actual
        finally:
            driver.close()

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
