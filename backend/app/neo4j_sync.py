"""Project the reviewed SQLite graph ledger into Neo4j for visualization/querying.

SQLite remains the source of truth. This module replaces only PaperTrail-owned
Neo4j data from a full snapshot and uses the stable IDs from ``GraphStore.export``.
"""

import argparse
import hashlib
import json
import os
import re
import uuid
from collections import defaultdict

from .config import GRAPH_PATH
from .graph import GraphStore
from .graph_ontology import EDGE_TYPES, NODE_TYPES


def derive_invoice_relationships(export):
    """Derive explainable invoice links from shared entities in a graph snapshot.

    These projection edges do not alter the SQLite claim ledger. Item relationships
    use exact case/whitespace-folded descriptions and make no product-identity claim.
    """
    nodes = {node["id"]: node for node in export["nodes"]}
    groups = defaultdict(lambda: defaultdict(list))
    for edge in export["edges"]:
        if edge["type"] == "ISSUED":
            groups["SAME_VENDOR_AS"][edge["source"]].append(edge["target"])
        elif edge["type"] == "BILLED_TO":
            groups["SAME_CUSTOMER_AS"][edge["target"]].append(edge["source"])

    links = defaultdict(set)
    for relation, entities in groups.items():
        for entity_id, invoice_ids in entities.items():
            unique = sorted(set(invoice_ids))
            for index, left in enumerate(unique):
                for right in unique[index + 1:]:
                    links[(left, right, relation)].add(entity_id)

    item_groups = defaultdict(lambda: defaultdict(set))
    for edge in export["edges"]:
        if edge["type"] != "CONTAINS":
            continue
        props = (nodes.get(edge["target"]) or {}).get("props") or {}
        description = props.get("description")
        if isinstance(description, str) and description.strip():
            key = re.sub(r"\s+", " ", description).strip().casefold()
            item_groups[key][edge["source"]].add(edge["target"])
    for description_key, invoices in item_groups.items():
        invoice_ids = sorted(invoices)
        for index, left in enumerate(invoice_ids):
            for right in invoice_ids[index + 1:]:
                description_id = hashlib.sha256(description_key.encode("utf-8")).hexdigest()[:16]
                links[(left, right, "SAME_ITEM_DESCRIPTION_AS")].add(description_id)

    derived = []
    for (left, right, relation), entity_ids in sorted(links.items()):
        for entity_id in sorted(entity_ids):
            derived.append({
                "id": f"{left}-{relation}->{right}:{entity_id}", "type": relation,
                "source": left, "target": right,
                "props": {"basis": relation.removeprefix("SAME_").removesuffix("_AS").lower(),
                          "shared_entity_id": entity_id},
                "support": list(nodes[left].get("support", [])) +
                           list(nodes[right].get("support", [])),
            })
    return derived


class Neo4jConfigurationError(RuntimeError):
    pass


def neo4j_properties(value):
    """Convert a JSON object into properties supported by Neo4j.

    Nested maps and lists of maps are retained as JSON strings. This preserves the
    complete source support/evidence payload without relying on APOC.
    """
    out = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, (str, bool, int, float)):
            out[key] = item
        elif isinstance(item, list) and all(
                element is not None and isinstance(element, (str, bool, int, float))
                for element in item):
            out[key] = item
        else:
            out[f"{key}_json"] = json.dumps(item, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), default=str)
    return out


def prepare_snapshot(export, generation=None):
    """Validate and shape a GraphStore export before sending any write to Neo4j."""
    generation = generation or str(uuid.uuid4())
    nodes = export.get("nodes")
    edges = export.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("graph export must contain node and edge arrays")

    ids, by_type = set(), defaultdict(list)
    for node in nodes:
        node_id, node_type = node.get("id"), node.get("type")
        if not isinstance(node_id, str) or not node_id or node_type not in NODE_TYPES:
            raise ValueError("graph export contains an invalid or unsupported node")
        if node_id.split(":", 1)[0] != node_type:
            raise ValueError(f"node id does not match its type: {node_id}")
        if node_id in ids:
            raise ValueError(f"duplicate graph node id: {node_id}")
        ids.add(node_id)
        properties = neo4j_properties(node.get("props") or {})
        properties.update({"papertrail_id": node_id, "papertrail_type": node_type,
                           "_sync_generation": generation})
        support = node.get("support") or []
        properties["support_json"] = json.dumps(support, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":"), default=str)
        properties["support_statuses"] = sorted({entry.get("review_status")
                                                 for entry in support
                                                 if entry.get("review_status")})
        by_type[node_type].append({"id": node_id, "properties": properties})

    edges_by_type = defaultdict(list)
    for edge in [*edges, *derive_invoice_relationships(export)]:
        edge_type = edge.get("type")
        source, target = edge.get("source"), edge.get("target")
        if edge_type not in EDGE_TYPES or source not in ids or target not in ids:
            raise ValueError("graph export contains an unsupported edge or missing endpoint")
        expected = EDGE_TYPES[edge_type]
        actual = (source.split(":", 1)[0], target.split(":", 1)[0])
        if actual != expected:
            raise ValueError(f"{edge_type} must connect {expected[0]} -> {expected[1]}")
        edge_props = neo4j_properties(edge.get("props") or {})
        edge_props.update({"papertrail_edge_id": edge["id"], "_papertrail": True,
                           "_sync_generation": generation})
        edge_props["support_json"] = json.dumps(edge.get("support") or [], ensure_ascii=False,
                                                sort_keys=True, separators=(",", ":"),
                                                default=str)
        edge_props["support_statuses"] = sorted({entry.get("review_status")
                                                 for entry in edge.get("support", [])
                                                 if entry.get("review_status")})
        edges_by_type[edge_type].append({"id": edge["id"], "source": source,
                                         "target": target, "properties": edge_props})
    return generation, by_type, edges_by_type


def _consume(tx, query, **parameters):
    result = tx.run(query, **parameters)
    consume = getattr(result, "consume", None)
    if consume:
        consume()


def sync_graph_export(export, driver, database=None, generation=None):
    """Atomically replace the PaperTrail projection with the supplied graph snapshot."""
    generation, nodes_by_type, edges_by_type = prepare_snapshot(export, generation)
    with driver.session(database=database) as session:
        session.run(
            "CREATE CONSTRAINT papertrail_node_id_unique IF NOT EXISTS "
            "FOR (n:PaperTrail) REQUIRE n.papertrail_id IS UNIQUE").consume()

        def write_snapshot(tx):
            for node_type, rows in sorted(nodes_by_type.items()):
                query = (f"UNWIND $rows AS row "
                         f"MERGE (n:PaperTrail:{node_type} {{papertrail_id: row.id}}) "
                         "SET n += row.properties")
                _consume(tx, query, rows=rows)

            for edge_type, rows in sorted(edges_by_type.items()):
                source_type, target_type = EDGE_TYPES[edge_type]
                query = ("UNWIND $rows AS row "
                         f"MATCH (s:PaperTrail:{source_type} {{papertrail_id: row.source}}) "
                         f"MATCH (t:PaperTrail:{target_type} {{papertrail_id: row.target}}) "
                         f"MERGE (s)-[r:{edge_type} {{papertrail_edge_id: row.id}}]->(t) "
                         "SET r += row.properties")
                _consume(tx, query, rows=rows)

            # Remove only relationships previously written by this integration.
            _consume(tx, "MATCH ()-[r]->() WHERE r._papertrail = true "
                          "AND r._sync_generation <> $generation DELETE r",
                     generation=generation)
            # Delete stale isolated projection nodes. A node with unrelated external
            # relationships is retained rather than detach-deleted.
            _consume(tx, "MATCH (n:PaperTrail) WHERE n._sync_generation <> $generation "
                          "AND NOT (n)--() DETACH DELETE n", generation=generation)

        session.execute_write(write_snapshot)
    return {"generation": generation, "nodes": sum(map(len, nodes_by_type.values())),
            "edges": sum(map(len, edges_by_type.values())), "reviewed_only": None}


def _restore_properties(properties, *, edge=False):
    properties = dict(properties or {})
    support_raw = properties.pop("support_json", "[]")
    try:
        support = json.loads(support_raw) if isinstance(support_raw, str) else []
    except json.JSONDecodeError:
        support = []
    ignored = {"papertrail_id", "papertrail_type", "papertrail_edge_id", "_papertrail",
               "_sync_generation", "support_statuses"}
    restored = {}
    for key, value in properties.items():
        if key in ignored:
            continue
        if key.endswith("_json"):
            try:
                value = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                pass
            restored[key[:-5]] = value
        else:
            restored[key] = value
    return restored, support if isinstance(support, list) else []


def export_neo4j_graph(driver, database=None, reviewed_only=False):
    """Read the owned Neo4j projection into the review UI's graph export contract."""
    params = {"reviewed_only": bool(reviewed_only),
              "reviewed_statuses": ["approved", "corrected"]}
    nodes, _, _ = driver.execute_query(
        "MATCH (n:PaperTrail) "
        "WHERE NOT $reviewed_only OR any(status IN coalesce(n.support_statuses, []) "
        "WHERE status IN $reviewed_statuses) "
        "RETURN properties(n) AS properties ORDER BY n.papertrail_id",
        database_=database, **params)
    node_rows = []
    for row in nodes:
        raw = dict(row["properties"])
        node_props, support = _restore_properties(raw)
        node_rows.append({"id": raw["papertrail_id"], "type": raw["papertrail_type"],
                          "props": node_props, "support": support})
    edges, _, _ = driver.execute_query(
        "MATCH (s:PaperTrail)-[r]->(t:PaperTrail) "
        "WHERE r._papertrail = true AND "
        "(NOT $reviewed_only OR any(status IN coalesce(r.support_statuses, []) "
        "WHERE status IN $reviewed_statuses)) "
        "RETURN properties(s).papertrail_id AS source, type(r) AS type, "
        "properties(t).papertrail_id AS target, properties(r) AS properties "
        "ORDER BY source, type, target",
        database_=database, **params)
    edge_rows = []
    for row in edges:
        raw = dict(row["properties"])
        edge_props, support = _restore_properties(raw, edge=True)
        edge_rows.append({"id": raw["papertrail_edge_id"], "type": row["type"],
                          "source": row["source"], "target": row["target"],
                          "props": edge_props, "support": support})
    return {"nodes": node_rows, "edges": edge_rows}


def create_driver(uri=None, username=None, password=None):
    uri = uri or os.environ.get("NEO4J_URI", "")
    username = username or os.environ.get("NEO4J_USERNAME", "neo4j")
    password = password if password is not None else os.environ.get("NEO4J_PASSWORD", "")
    if not uri or not password:
        raise Neo4jConfigurationError("Set NEO4J_URI and NEO4J_PASSWORD; username defaults to neo4j")
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise Neo4jConfigurationError(
            "Neo4j sync needs the optional driver; install backend/requirements-neo4j.txt") from exc
    return GraphDatabase.driver(uri, auth=(username, password))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync PaperTrail's SQLite graph to Neo4j")
    parser.add_argument("--graph", default=str(GRAPH_PATH), help="SQLite graph database path")
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE") or None)
    parser.add_argument("--reviewed-only", action="store_true",
                        help="project only facts supported by approved/corrected extractions")
    args = parser.parse_args(argv)
    store = GraphStore(args.graph)
    export = store.export(reviewed_only=args.reviewed_only)
    driver = create_driver()
    try:
        driver.verify_connectivity()
        result = sync_graph_export(export, driver, database=args.database)
    finally:
        driver.close()
    result["reviewed_only"] = args.reviewed_only
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
