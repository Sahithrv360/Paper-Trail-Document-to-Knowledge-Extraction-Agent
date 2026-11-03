"""Export the current Paper Trail SQLite graph as Cytoscape-compatible GraphML.

Run from the repository root with ``python -m backend.app.cytoscape_export``.
"""
import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import GRAPH_PATH
from .graph import GraphStore

NS = "http://graphml.graphdrawing.org/xmlns"
ET.register_namespace("", NS)


def export_graphml(destination, reviewed_only=False):
    graph = GraphStore(GRAPH_PATH).export(reviewed_only=reviewed_only)
    from .neo4j_sync import derive_direct_invoice_relationships, derive_shared_item_hubs
    hub_nodes, hub_edges = derive_shared_item_hubs(graph)
    direct_edges = derive_direct_invoice_relationships(graph, hub_nodes, hub_edges)
    graph = {**graph, "nodes": [*graph["nodes"], *hub_nodes],
             "edges": [*graph["edges"], *hub_edges, *direct_edges]}
    root = ET.Element(f"{{{NS}}}graphml")
    for key, target, name in [
        ("type", "all", "type"), ("label", "node", "label"),
        ("name", "node", "name"),
        ("props", "all", "props"), ("review_status", "node", "review_status"),
        ("source", "edge", "source"), ("target", "edge", "target"),
    ]:
        ET.SubElement(root, f"{{{NS}}}key", id=key, **{"for": target}, attr_name=name,
                      attr_type="string")
    network = ET.SubElement(root, f"{{{NS}}}graph", id="PaperTrail", edgedefault="directed")
    ids = set()
    for node in graph["nodes"]:
        if node["type"] == "Document":
            continue
        props = node["props"]
        invoice_no = props.get("invoice_number")
        label = str(invoice_no or ("Invoice number not read" if node["type"] == "Invoice"
                                   else props.get("name") or props.get("description")
                                   or node["id"]))
        elem = ET.SubElement(network, f"{{{NS}}}node", id=str(node["id"]))
        ids.add(str(node["id"]))
        ET.SubElement(elem, f"{{{NS}}}data", key="type").text = str(node["type"])
        ET.SubElement(elem, f"{{{NS}}}data", key="label").text = label
        ET.SubElement(elem, f"{{{NS}}}data", key="name").text = label
        ET.SubElement(elem, f"{{{NS}}}data", key="props").text = json.dumps(props, ensure_ascii=False)
        ET.SubElement(elem, f"{{{NS}}}data", key="review_status").text = str(props.get("review_status", ""))
    for edge in graph["edges"]:
        if edge["source"] not in ids or edge["target"] not in ids:
            continue
        elem = ET.SubElement(network, f"{{{NS}}}edge", id=str(edge["id"]),
                             source=str(edge["source"]), target=str(edge["target"]))
        ET.SubElement(elem, f"{{{NS}}}data", key="type").text = str(edge["type"])
        ET.SubElement(elem, f"{{{NS}}}data", key="props").text = json.dumps(edge["props"], ensure_ascii=False)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(target, encoding="utf-8", xml_declaration=True)
    return len(graph["nodes"]), len(graph["edges"]), target.resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/papertrail.graphml")
    parser.add_argument("--reviewed-only", action="store_true")
    args = parser.parse_args(argv)
    nodes, edges, path = export_graphml(args.output, args.reviewed_only)
    print(f"Exported {nodes} nodes and {edges} edges to {path}")


if __name__ == "__main__":
    main()
