"""JSONL-backed graph store loaded into a NetworkX MultiDiGraph."""

import json
from pathlib import Path
from typing import Literal

import networkx as nx

from kg_agent.models import (Chunk, Community, Edge, GraphSchema, Node, Path as KGPath,
                             RelationSummary, Subgraph, TypeSummary)


class FileGraphStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.nodes = self._read("nodes.jsonl", Node)
        self.edges = self._read("edges.jsonl", Edge)
        self.chunks = self._read("chunks.jsonl", Chunk)
        self._ensure_unique(self.nodes, "node")
        self._ensure_unique(self.edges, "edge")
        self._ensure_unique(self.chunks, "chunk")
        self.node_by_id = {item.id: item for item in self.nodes}
        self.edge_by_id = {item.id: item for item in self.edges}
        self.chunk_by_id = {item.id: item for item in self.chunks}
        self.graph = nx.MultiDiGraph()
        for node in self.nodes:
            self.graph.add_node(node.id, record=node)
        for edge in self.edges:
            if edge.source not in self.node_by_id or edge.target not in self.node_by_id:
                raise ValueError(f"Edge {edge.id} references a missing node")
            self.graph.add_edge(edge.source, edge.target, key=edge.id, record=edge)

    def _read(self, filename, model):
        path = self.directory / filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing graph data file: {path}")
        items = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                items.append(model.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"Invalid {filename}:{line_number}: {exc}") from exc
        return items

    @staticmethod
    def _ensure_unique(items, label):
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {label} IDs")

    def schema(self) -> GraphSchema:
        entity_types = []
        for kind in sorted({n.type for n in self.nodes}):
            items = [n for n in self.nodes if n.type == kind]
            entity_types.append(TypeSummary(type=kind, count=len(items),
                samples=[n.name for n in items[:3]],
                property_keys=sorted({key for n in items for key in n.properties}),
                description=next((n.properties.get("type_description", "") for n in items
                                  if n.properties.get("type_description")), "")))
        relation_types = []
        for kind in sorted({e.type for e in self.edges}):
            items = [e for e in self.edges if e.type == kind]
            first = items[0]
            relation_types.append(RelationSummary(type=kind, count=len(items),
                domain=sorted({self.node_by_id[e.source].type for e in items}),
                range=sorted({self.node_by_id[e.target].type for e in items}),
                sample_triple=f"{self.node_by_id[first.source].name} -[{kind}]-> "
                              f"{self.node_by_id[first.target].name}"))
        return GraphSchema(entity_types=entity_types, relation_types=relation_types,
                           node_count=len(self.nodes), edge_count=len(self.edges),
                           chunk_count=len(self.chunks))

    def get_node(self, node_id: str) -> Node:
        try:
            return self.node_by_id[node_id]
        except KeyError as exc:
            raise KeyError(f"Unknown node ID: {node_id}") from exc

    def neighbors(self, node_id: str, rel_types: list[str] | None = None,
                  direction: Literal["in", "out", "both"] = "both",
                  limit: int = 100) -> list[Edge]:
        if node_id not in self.node_by_id:
            raise KeyError(f"Unknown node ID: {node_id}")
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []
        selected = []
        seen = set()
        candidates = []
        if direction in ("out", "both"):
            candidates.extend(self.graph.out_edges(node_id, keys=True, data=True))
        if direction in ("in", "both"):
            candidates.extend(self.graph.in_edges(node_id, keys=True, data=True))
        for _, _, _, attrs in candidates:
            record = attrs["record"]
            if (rel_types is None or record.type in rel_types) and record.id not in seen:
                selected.append(record)
                seen.add(record.id)
            if len(selected) >= limit:
                break
        return selected

    def subgraph(self, seeds: list[str], hops: int = 1,
                 rel_types: list[str] | None = None,
                 max_nodes: int = 100) -> Subgraph:
        if hops < 0 or max_nodes < 1:
            raise ValueError("hops must be non-negative and max_nodes positive")
        unknown = [seed for seed in seeds if seed not in self.node_by_id]
        if unknown:
            raise KeyError(f"Unknown seed node IDs: {unknown}")
        visited = list(dict.fromkeys(seeds))[:max_nodes]
        frontier = list(visited)
        chosen_edges: dict[str, Edge] = {}
        for _ in range(hops):
            next_frontier = []
            for node_id in frontier:
                for edge in self.neighbors(node_id, rel_types, "both"):
                    chosen_edges[edge.id] = edge
                    other = edge.target if edge.source == node_id else edge.source
                    if other not in visited and len(visited) < max_nodes:
                        visited.append(other)
                        next_frontier.append(other)
            frontier = next_frontier
        valid_edges = [edge for edge in chosen_edges.values()
                       if edge.source in visited and edge.target in visited]
        return Subgraph(nodes=[self.node_by_id[nid] for nid in visited], edges=valid_edges)

    def find_paths(self, a: str, b: str, max_hops: int = 4,
                   k: int = 5) -> list[KGPath]:
        if a not in self.node_by_id or b not in self.node_by_id:
            return []
        if max_hops < 0 or k < 0:
            raise ValueError("max_hops and k must be non-negative")
        if k == 0:
            return []
        paths = []
        iterator = nx.all_simple_paths(self.graph.to_undirected(), a, b,
                                       cutoff=max_hops)
        for node_ids in iterator:
            edge_ids = []
            for source, target in zip(node_ids, node_ids[1:]):
                data = self.graph.get_edge_data(source, target) or self.graph.get_edge_data(target, source)
                edge_ids.append(next(iter(data.values()))["record"].id)
            paths.append(KGPath(node_ids=node_ids, edge_ids=edge_ids))
            if len(paths) >= k:
                break
        return paths

    def chunks_for(self, node_or_edge_ids: list[str]) -> list[Chunk]:
        chunk_ids = set()
        for item_id in node_or_edge_ids:
            record = self.node_by_id.get(item_id) or self.edge_by_id.get(item_id)
            if record:
                chunk_ids.update(record.chunk_ids)
        return [self.chunk_by_id[cid] for cid in chunk_ids if cid in self.chunk_by_id]

    def all_chunks(self):
        return iter(self.chunks)

    def all_nodes(self):
        return iter(self.nodes)

    def communities(self) -> list[Community]:
        undirected = nx.Graph(self.graph)
        try:
            detected = nx.algorithms.community.louvain_communities(
                undirected, seed=17, weight=None)
        except AttributeError:
            detected = nx.connected_components(undirected)
        components = sorted((set(group) for group in detected),
                            key=lambda members: (-len(members), min(members)))
        return [Community(id=f"community-{index + 1}", node_ids=sorted(members))
                for index, members in enumerate(components)]
