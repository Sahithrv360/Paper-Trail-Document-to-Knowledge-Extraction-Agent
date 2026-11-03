"""Adapter that runs kg-agent over Paper Trail's live, read-only graph export."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


class PaperTrailAgentStore:
    """Expose the kg-agent store contract without creating a second graph database."""

    def __init__(self, export):
        import networkx as nx
        from kg_agent.models import Chunk, Edge, Node

        self.nodes = []
        for raw in export.get("nodes", []):
            props = dict(raw.get("props") or {})
            name = str(props.get("invoice_number") or props.get("name") or
                       props.get("filename") or raw["id"])
            description = str(props.get("summary_text") or props.get("description") or "")
            if raw.get("type") == "ConversationSummary" and props.get("synthetic") is True:
                description = description.split("\nMessages:", 1)[0]
            aliases = [str(value) for value in (props.get("aliases") or []) if value]
            self.nodes.append(Node(id=str(raw["id"]), type=str(raw.get("type", "Entity")),
                name=name, aliases=aliases, description=description, properties=props))
        self.node_by_id = {item.id: item for item in self.nodes}
        self.edges = []
        for raw in export.get("edges", []):
            if raw.get("source") not in self.node_by_id or raw.get("target") not in self.node_by_id:
                continue
            self.edges.append(Edge(id=str(raw["id"]), source=str(raw["source"]),
                target=str(raw["target"]), type=str(raw.get("type", "RELATED_TO")),
                properties=dict(raw.get("props") or {})))
        self.edge_by_id = {item.id: item for item in self.edges}
        self.chunks = []
        for node in self.nodes:
            fields = "; ".join(f"{key}: {value}" for key, value in node.properties.items()
                                if key not in {"summary_text", "chunk_text"} and value is not None
                                and isinstance(value, (str, int, float, bool)))
            text = "; ".join(value for value in (node.name, node.description, fields) if value)
            if text:
                chunk_id = f"chunk-{node.id}"
                self.chunks.append(Chunk(id=chunk_id, doc_id=node.id, text=text))
                node.chunk_ids.append(chunk_id)
        self.chunk_by_id = {item.id: item for item in self.chunks}
        for edge in self.edges:
            edge.chunk_ids = list(dict.fromkeys(self.node_by_id[edge.source].chunk_ids +
                                                self.node_by_id[edge.target].chunk_ids))
        self.graph = nx.MultiDiGraph()
        for node in self.nodes:
            self.graph.add_node(node.id, record=node)
        for edge in self.edges:
            self.graph.add_edge(edge.source, edge.target, key=edge.id, record=edge)

    def all_nodes(self):
        return iter(self.nodes)

    def all_chunks(self):
        return iter(self.chunks)

    def schema(self):
        from kg_agent.models import GraphSchema, RelationSummary, TypeSummary

        types = [TypeSummary(type=kind, count=sum(n.type == kind for n in self.nodes),
                    samples=[n.name for n in self.nodes if n.type == kind][:3],
                    property_keys=sorted({key for n in self.nodes if n.type == kind
                                          for key in n.properties}))
                 for kind in sorted({n.type for n in self.nodes})]
        relations = []
        for kind in sorted({edge.type for edge in self.edges}):
            edges = [edge for edge in self.edges if edge.type == kind]
            first = edges[0]
            relations.append(RelationSummary(type=kind, count=len(edges),
                domain=sorted({self.node_by_id[e.source].type for e in edges}),
                range=sorted({self.node_by_id[e.target].type for e in edges}),
                sample_triple=f"{self.node_by_id[first.source].name} -[{kind}]-> "
                              f"{self.node_by_id[first.target].name}"))
        return GraphSchema(entity_types=types, relation_types=relations,
            node_count=len(self.nodes), edge_count=len(self.edges), chunk_count=len(self.chunks))

    def subgraph(self, seeds, hops=1, rel_types=None, max_nodes=100):
        from kg_agent.models import Subgraph

        visited = list(dict.fromkeys(seed for seed in seeds if seed in self.node_by_id))[:max_nodes]
        frontier, chosen = list(visited), {}
        for _ in range(hops):
            next_frontier = []
            for node_id in frontier:
                for edge in self.neighbors(node_id, rel_types):
                    chosen[edge.id] = edge
                    other = edge.target if edge.source == node_id else edge.source
                    if other not in visited and len(visited) < max_nodes:
                        visited.append(other)
                        next_frontier.append(other)
            frontier = next_frontier
        return Subgraph(nodes=[self.node_by_id[i] for i in visited],
            edges=[e for e in chosen.values() if e.source in visited and e.target in visited])

    def neighbors(self, node_id, rel_types=None, direction="both", limit=100):
        candidates = []
        if direction in ("out", "both"):
            candidates.extend(self.graph.out_edges(node_id, keys=True, data=True))
        if direction in ("in", "both"):
            candidates.extend(self.graph.in_edges(node_id, keys=True, data=True))
        found, seen = [], set()
        for *_, attrs in candidates:
            edge = attrs["record"]
            if edge.id not in seen and (rel_types is None or edge.type in rel_types):
                found.append(edge)
                seen.add(edge.id)
                if len(found) >= limit:
                    break
        return found

    def chunks_for(self, ids):
        chunk_ids = set()
        for item_id in ids:
            record = self.node_by_id.get(item_id) or self.edge_by_id.get(item_id)
            if record:
                chunk_ids.update(record.chunk_ids)
        return [self.chunk_by_id[cid] for cid in chunk_ids if cid in self.chunk_by_id]

    def find_paths(self, a, b, max_hops=4, k=5):
        from kg_agent.models import Path

        if a not in self.node_by_id or b not in self.node_by_id:
            return []
        paths = []
        for nodes in __import__("networkx").all_simple_paths(
                self.graph.to_undirected(), a, b, cutoff=max_hops):
            edge_ids = []
            for source, target in zip(nodes, nodes[1:]):
                edges = self.graph.get_edge_data(source, target) or self.graph.get_edge_data(target, source)
                edge_ids.append(next(iter(edges.values()))["record"].id)
            paths.append(Path(node_ids=nodes, edge_ids=edge_ids))
            if len(paths) >= k:
                break
        return paths

    def communities(self):
        from kg_agent.models import Community

        groups = __import__("networkx").connected_components(__import__("networkx").Graph(self.graph))
        return [Community(id=f"community-{index}", node_ids=sorted(group))
                for index, group in enumerate(groups, 1)]


def run_papertrail_agent(export, question, *, generate=False, history=None, on_progress=None):
    """Run the workflow with the explicitly configured local or OpenRouter model."""
    from kg_agent.runtime.agent import BusinessQAAgent

    store = PaperTrailAgentStore(export)
    llm = None
    model_provider = "none"
    model_name = None
    if generate:
        from kg_agent.config import AgentConfig
        from kg_agent.llm import OpenAICompatibleModel

        model_provider = os.environ.get("AGENT_LLM_PROVIDER", "ollama").strip().lower()
        if model_provider == "openrouter":
            api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("Set OPENROUTER_API_KEY in .env to use the OpenRouter agent model")
            config = AgentConfig(llm_provider="openrouter",
                llm_model=os.environ.get("OPENROUTER_MODEL", "qwen/qwen3.8-flash"),
                openrouter_api_key=api_key,
                openrouter_base_url="https://openrouter.ai/api/v1",
                max_output_tokens=int(os.environ.get("AGENT_LLM_MAX_OUTPUT_TOKENS", "1024")),
                request_timeout_seconds=float(os.environ.get("AGENT_LLM_TIMEOUT_SECONDS", "35")),
                max_retries=int(os.environ.get("AGENT_LLM_MAX_RETRIES", "0")))
        elif model_provider == "ollama":
            ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
            parsed_host = urlsplit(ollama_host)
            if parsed_host.scheme != "http" or parsed_host.hostname not in {
                    "127.0.0.1", "localhost", "::1"}:
                raise RuntimeError("Agent mode requires OLLAMA_HOST to use a local loopback address")
            config = AgentConfig(llm_provider="ollama",
                llm_model=os.environ.get("GRAPH_RAG_MODEL") or os.environ.get("OLLAMA_MODEL", "qwen3:4b"),
                ollama_base_url=ollama_host.rstrip("/") + "/v1",
                max_output_tokens=int(os.environ.get("AGENT_LLM_MAX_OUTPUT_TOKENS", "2048")))
        else:
            raise RuntimeError("AGENT_LLM_PROVIDER must be ollama or openrouter")
        model_name = config.llm_model
        llm = OpenAICompatibleModel(config)
    # Keep planning, evidence ranking, and verification deterministic/local. Call
    # the hosted model once for final answer phrasing to reduce latency and cost.
    agent = BusinessQAAgent(store, runs_dir=os.environ.get("PAPERTRAIL_AGENT_RUNS", "runs/agentic_rag"),
                            context_token_budget=int(os.environ.get("AGENT_CONTEXT_TOKEN_BUDGET", "2200")))
    agent.answerer.llm = llm
    response = agent.run(question, mode="agent", history=history,
                         progress_callback=on_progress)
    result = response.model_dump(mode="json")
    result["model_provider"] = model_provider
    result["model_name"] = model_name
    return result
