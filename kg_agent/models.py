"""Pydantic v2 contracts shared by graph stores and runtime components."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)
    chunk_ids: list[str] = Field(default_factory=list)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source: str
    target: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    chunk_ids: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    doc_id: str
    page: int | None = Field(default=None, ge=1)
    text: str


class TypeSummary(BaseModel):
    type: str
    count: int
    samples: list[str] = Field(default_factory=list)
    property_keys: list[str] = Field(default_factory=list)
    description: str = ""


class RelationSummary(BaseModel):
    type: str
    count: int
    domain: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)
    sample_triple: str = ""
    description: str = ""


class GraphSchema(BaseModel):
    entity_types: list[TypeSummary]
    relation_types: list[RelationSummary]
    node_count: int
    edge_count: int
    chunk_count: int


class Subgraph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]


class Path(BaseModel):
    node_ids: list[str]
    edge_ids: list[str]


class Community(BaseModel):
    id: str
    node_ids: list[str]
    summary: str = ""


class SystemPromptArtifact(BaseModel):
    prompt: str
    version: int
    schema_hash: str
    created_at: str
    model: str | None = None
    refined: bool = False


class Evidence(BaseModel):
    id: str
    source_tool: str
    kind: str
    text: str
    score: float = 0.0
    doc_id: str | None = None
    page: int | None = None
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    citation_id: str | None = None


class EntityCandidate(BaseModel):
    node_id: str
    name: str
    score: float


class LinkedEntity(BaseModel):
    mention: str
    node_id: str | None = None
    confidence: float = 0.0
    candidates: list[EntityCandidate] = Field(default_factory=list)


class SubQuestion(BaseModel):
    question: str
    tools: list[Literal["keyword", "dense", "graph"]] = Field(default_factory=list)


class GraphQuery(BaseModel):
    seeds: list[str] = Field(default_factory=list)
    rel_types: list[str] | None = None
    hops: int = Field(default=2, ge=1, le=5)
    direction: Literal["in", "out", "both"] = "both"
    mode: Literal["local", "global", "cypher"] = "local"


class QueryPlan(BaseModel):
    original: str
    standalone_question: str
    intent: Literal["lookup", "relationship", "multi_hop", "aggregation",
                    "comparison", "summary_global", "definition"]
    entities: list[LinkedEntity] = Field(default_factory=list)
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    keyword_query: str = ""
    dense_query: str = ""
    graph_query: GraphQuery | None = None
    tools: list[Literal["keyword", "dense", "graph"]] = Field(default_factory=list)
    needs_clarification: str | None = None
    rationale: str = ""


class ContextItem(BaseModel):
    citation_id: str
    evidence: Evidence


class BuiltContext(BaseModel):
    items: list[ContextItem] = Field(default_factory=list)
    text: str = ""
    token_estimate: int = 0
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class AnswerDraft(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_info: str = ""


class VerificationResult(BaseModel):
    supported: bool
    complete: bool
    issues: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AgentResponse(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    graph_paths: list[Path] = Field(default_factory=list)
    confidence: float = 0.0
    query_plan: QueryPlan
    trace: dict[str, Any] = Field(default_factory=dict)
