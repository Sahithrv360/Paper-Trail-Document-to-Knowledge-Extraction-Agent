"""Persistent JSON embedding index for document chunks and entity descriptions."""

import json
import math
from pathlib import Path

from kg_agent.models import Evidence


class PersistentDenseIndex:
    def __init__(self, path: Path | str, embedder):
        self.path = Path(path)
        self.embedder = embedder
        self.records: list[dict] = []

    def build(self, store) -> int:
        sources = []
        for chunk in store.all_chunks():
            sources.append({"id": chunk.id, "kind": "chunk", "text": chunk.text,
                            "doc_id": chunk.doc_id, "page": chunk.page,
                            "chunk_ids": [chunk.id], "node_ids": []})
        for node in store.all_nodes():
            text = f"{node.name}. {node.description}. " + "; ".join(
                f"{key}: {value}" for key, value in node.properties.items())
            sources.append({"id": node.id, "kind": "entity", "text": text,
                            "doc_id": None, "page": None,
                            "chunk_ids": node.chunk_ids, "node_ids": [node.id]})
        vectors = self.embedder.embed([row["text"] for row in sources])
        if len(vectors) != len(sources):
            raise ValueError("Embedding provider returned a different vector count")
        self.records = [{**row, "vector": vector} for row, vector in zip(sources, vectors)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"version": 1, "records": self.records}),
                             encoding="utf-8")
        return len(self.records)

    def load(self) -> int:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("records"), list):
            raise ValueError("Unsupported dense index format")
        self.records = payload["records"]
        return len(self.records)

    def search(self, query: str, limit: int = 8) -> list[Evidence]:
        if not self.records:
            self.load()
        if limit <= 0:
            return []
        query_vector = self.embedder.embed([query])[0]
        scored = []
        for row in self.records:
            vector = row["vector"]
            if len(vector) != len(query_vector):
                continue
            denominator = math.sqrt(sum(v * v for v in vector) *
                                    sum(v * v for v in query_vector))
            score = sum(a * b for a, b in zip(vector, query_vector)) / denominator if denominator else 0
            scored.append((score, row))
        return [Evidence(id=f"dense:{row['id']}", source_tool="dense", kind=row["kind"],
                         text=row["text"], score=score, doc_id=row["doc_id"],
                         page=row["page"], node_ids=row["node_ids"],
                         chunk_ids=row["chunk_ids"])
                for score, row in sorted(scored, key=lambda pair: (-pair[0], pair[1]["id"]))[:limit]]
