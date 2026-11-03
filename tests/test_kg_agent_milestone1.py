import json
from pathlib import Path

from pydantic import BaseModel

from kg_agent.config import AgentConfig
from kg_agent.llm import FakeLLM, repair_json, strip_think_blocks
from kg_agent.kg.file_store import FileGraphStore
from kg_agent.kg.sample_data import build_sample
from kg_agent.factory.prompt_generator import BusinessContext, generate_system_prompt
from kg_agent.runtime.tools.keyword import keyword_search
from kg_agent.runtime.tools.graph import graph_search, graph_global_search
from kg_agent.runtime.tools.dense import PersistentDenseIndex
from kg_agent.runtime.entity_linker import link_entity
from kg_agent.runtime.query_transformer import QueryTransformer
from kg_agent.runtime.fusion import reciprocal_rank_fusion
from kg_agent.runtime.rerank import EvidenceReranker
from kg_agent.runtime.context_builder import build_context
from kg_agent.runtime.answerer import AnswerGenerator
from kg_agent.models import Evidence
from kg_agent.runtime.agent import BusinessQAAgent
from kg_agent.runtime.tools.graph import deterministic_aggregation
from kg_agent.eval.runner import evaluate, load_dataset, render_report


class Reply(BaseModel):
    ok: bool


def test_fake_model_returns_schema_valid_json_offline():
    model = FakeLLM(['<think>hidden</think> ```json\n{"ok": true}\n```'])
    result = model.complete([], json_mode=True, response_model=Reply)
    assert json.loads(repair_json(result)) == {"ok": True}
    assert len(model.calls) == 1


def test_json_repair_handles_preface_and_braces_in_strings():
    assert repair_json('Answer: {"text":"a } brace"}') == '{"text":"a } brace"}'


def test_think_block_removed():
    assert strip_think_blocks("<think>reasoning</think>answer") == "answer"


def test_config_defaults_to_local_ollama():
    config = AgentConfig()
    assert config.llm_provider == "ollama"
    assert config.llm_base_url == "http://localhost:11434/v1"


def test_file_store_loads_sample_counts_and_schema(tmp_path):
    build_sample(tmp_path)
    graph = FileGraphStore(tmp_path)
    schema = graph.schema()
    assert (schema.node_count, schema.edge_count, schema.chunk_count) == (40, 60, 30)
    assert {item.type for item in schema.entity_types} == {
        "Supplier", "Contract", "Invoice", "Customer", "Item"}


def test_file_store_neighbors_subgraph_and_paths(tmp_path):
    build_sample(tmp_path)
    graph = FileGraphStore(tmp_path)
    result = graph.subgraph(["invoice-PT-2026001"], hops=2)
    assert {node.id for node in result.nodes} >= {
        "invoice-PT-2026001", "supplier-01", "customer-01"}
    paths = graph.find_paths("supplier-01", "customer-01", max_hops=2)
    assert paths and len(paths[0].edge_ids) <= 2
    assert graph.neighbors("supplier-01", direction="out")


def test_file_store_resolves_source_chunks(tmp_path):
    build_sample(tmp_path)
    graph = FileGraphStore(tmp_path)
    chunks = graph.chunks_for(["invoice-PT-2026001", "edge-billed-to-01"])
    assert any(chunk.doc_id == "invoice-PT-2026001" for chunk in chunks)


def test_prompt_factory_covers_all_schema_types(tmp_path):
    build_sample(tmp_path / "kg")
    graph = FileGraphStore(tmp_path / "kg")
    context = BusinessContext(domain="Procurement", goals=["Use evidence"])
    artifact = generate_system_prompt(graph.schema(), context,
                                      artifacts_dir=tmp_path / "artifacts")
    for summary in graph.schema().entity_types + graph.schema().relation_types:
        assert summary.type in artifact.prompt
    assert artifact.schema_hash and artifact.version == 1
    assert (tmp_path / "artifacts" / "system_prompt.v1.md").is_file()
    assert (tmp_path / "artifacts" / "system_prompt.v1.json").is_file()


def test_prompt_refinement_rejects_unknown_types(tmp_path):
    class InventingModel:
        def complete(self, messages, **kwargs):
            return json.dumps({"prompt": "Use (Supplier)-[INVENTED_RELATION]->(Contract).",
                "entity_types": ["Supplier", "Contract", "Warehouse"],
                "relation_types": ["INVENTED_RELATION"]})

    build_sample(tmp_path / "kg")
    graph = FileGraphStore(tmp_path / "kg")
    artifact = generate_system_prompt(graph.schema(), BusinessContext(domain="Procurement"),
        refine=True, llm=InventingModel(), artifacts_dir=tmp_path / "artifacts")
    assert not artifact.refined
    assert "INVENTED_RELATION" not in artifact.prompt


def test_prompt_refinement_accepts_schema_only_types(tmp_path):
    build_sample(tmp_path / "kg")
    graph = FileGraphStore(tmp_path / "kg")
    schema = graph.schema()

    class SafeModel:
        def complete(self, messages, **kwargs):
            return json.dumps({"prompt": "Procurement assistant. (Supplier)-[HAS_CONTRACT]->(Contract).",
                "entity_types": [item.type for item in schema.entity_types],
                "relation_types": [item.type for item in schema.relation_types]})

    artifact = generate_system_prompt(schema, BusinessContext(domain="Procurement"),
        refine=True, llm=SafeModel(), artifacts_dir=tmp_path / "artifacts")
    assert artifact.refined
    assert "HAS_CONTRACT" in artifact.prompt


def test_keyword_and_local_graph_tools_return_cited_evidence(tmp_path):
    build_sample(tmp_path)
    graph = FileGraphStore(tmp_path)
    keyword = keyword_search(graph, "invoice-PT-2026001", limit=3)
    assert keyword and keyword[0].source_tool == "keyword"
    triples = graph_search(graph, ["supplier-01"], hops=2)
    assert any("HAS_CONTRACT" in item.text for item in triples)
    assert all(item.edge_ids and item.chunk_ids for item in triples)


def test_dense_index_persists_and_searches_with_local_fake_embedder(tmp_path):
    class FakeEmbeddings:
        def embed(self, texts):
            return [[float(len(text)), float(text.casefold().count("invoice")), 1.0]
                    for text in texts]

    build_sample(tmp_path / "kg")
    graph = FileGraphStore(tmp_path / "kg")
    index = PersistentDenseIndex(tmp_path / "dense.json", FakeEmbeddings())
    assert index.build(graph) == 70
    index.records = []
    results = index.search("invoice total", limit=4)
    assert results and results[0].source_tool == "dense"
    assert index.load() == 70


def test_global_graph_search_summarizes_relevant_communities(tmp_path):
    build_sample(tmp_path)
    graph = FileGraphStore(tmp_path)
    results = graph_global_search(graph, "Northstar supplier", limit=2)
    assert results and results[0].kind == "community_summary"
    assert results[0].node_ids


def test_entity_linker_exact_and_fuzzy_matches(tmp_path):
    build_sample(tmp_path)
    graph = FileGraphStore(tmp_path)
    exact = link_entity(graph, "MSA-2026-01")
    fuzzy = link_entity(graph, "Northstar Suppy 01")
    assert exact.node_id == "contract-2026-01" and exact.confidence == 1.0
    assert fuzzy.node_id == "supplier-01" and fuzzy.confidence >= 0.85
    assert len(fuzzy.candidates) == 3


def test_query_transformer_expands_glossary_and_routes_graph_questions(tmp_path):
    build_sample(tmp_path)
    graph = FileGraphStore(tmp_path)
    context = BusinessContext(domain="Procurement", glossary={"MSA": "Master Service Agreement"})
    transformer = QueryTransformer(graph, business_context=context)
    plan = transformer.transform("How many invoices reference MSA-2026-01 under the MSA?")
    assert plan.intent == "aggregation"
    assert "graph" in plan.tools
    assert plan.graph_query and plan.graph_query.seeds == ["contract-2026-01"]
    assert "Master Service Agreement" in plan.standalone_question
    assert plan.sub_questions


def test_query_transformer_resolves_simple_followup_context(tmp_path):
    build_sample(tmp_path)
    transformer = QueryTransformer(FileGraphStore(tmp_path))
    plan = transformer.transform("Which others?", history=[
        {"role": "user", "content": "Which suppliers have contracts expiring soon?"}])
    assert "suppliers have contracts expiring soon" in plan.standalone_question


def test_rrf_fuses_duplicate_chunks_and_preserves_support_ids():
    left = Evidence(id="kw:c1", source_tool="keyword", kind="chunk",
        text="Invoice details", doc_id="doc-1", chunk_ids=["c1"], node_ids=["n1"])
    right = Evidence(id="dense:c1", source_tool="dense", kind="chunk",
        text="Invoice details", doc_id="doc-1", chunk_ids=["c1"], edge_ids=["e1"])
    result = reciprocal_rank_fusion([[left], [right]])
    assert len(result) == 1
    assert result[0].source_tool == "dense+keyword"
    assert result[0].node_ids == ["n1"] and result[0].edge_ids == ["e1"]


def test_reranker_and_context_prioritize_graph_triples():
    chunk = Evidence(id="c", source_tool="keyword", kind="chunk", text="invoice background",
                     score=0.9, chunk_ids=["chunk-1"])
    triple = Evidence(id="t", source_tool="graph", kind="triple",
                      text="Supplier HAS_CONTRACT Contract", score=0.1, edge_ids=["edge-1"])
    ranked = EvidenceReranker().rerank("supplier contract relationship", [chunk, triple])
    assert ranked[0].id == "t"
    exact = Evidence(id="exact", source_tool="graph", kind="triple",
                     text="(Northstar Supply 01) -[ISSUED_INVOICE]-> (invoice-PT-2026001)")
    unrelated = Evidence(id="other", source_tool="graph", kind="triple",
                         text="(Cedar Ridge Manufacturing 01) -[BILLED_TO]-> (invoice-PT-2026001)")
    assert EvidenceReranker().rerank("Which supplier issued invoice-PT-2026001?",
                                     [unrelated, exact])[0].id == "exact"
    context = build_context([chunk, triple], token_budget=20, relationship_question=True)
    assert context.items[0].evidence.kind == "triple"
    assert "[T1]" in context.text


def test_answer_generator_accepts_only_supplied_citations():
    context = build_context([Evidence(id="e", source_tool="keyword", kind="chunk",
        text="Invoice total is 1250 USD", chunk_ids=["chunk-1"])])
    fake = FakeLLM(['{"answer":"Total is 1250 USD.","citations":["C1","FAKE"],'
                     '"reasoning_summary":"matched source","confidence":0.9,"missing_info":""}'])
    answer = AnswerGenerator(fake).answer("What is the total?", context)
    assert answer.citations == ["C1"]
    assert answer.confidence == 0.9


def test_agent_runs_graph_grounded_answer_and_persists_trace(tmp_path):
    build_sample(tmp_path / "kg")
    graph = FileGraphStore(tmp_path / "kg")
    agent = BusinessQAAgent(graph, runs_dir=tmp_path / "runs")
    response = agent.run("Which supplier issued invoice-PT-2026001?", mode="agent")
    assert response.citations
    assert response.evidence
    assert response.query_plan.entities
    assert Path(response.trace["trace_file"]).is_file()
    assert response.trace["steps"][-1]["step"] == "retrieve_answer_verify"


def test_agent_bounds_modes_and_returns_not_found_without_evidence(tmp_path):
    build_sample(tmp_path / "kg")
    agent = BusinessQAAgent(FileGraphStore(tmp_path / "kg"), runs_dir=tmp_path / "runs")
    response = agent.run("What is the policy for lunar mining?", mode="keyword")
    assert not response.citations
    assert response.confidence == 0.0
    try:
        agent.run("test", mode="unknown")
        assert False, "invalid mode should fail"
    except ValueError:
        pass


def test_deterministic_graph_aggregation_returns_supported_count_and_sum(tmp_path):
    build_sample(tmp_path)
    graph = FileGraphStore(tmp_path)
    count = deterministic_aggregation(graph, "How many invoices are there?")
    punctuated = deterministic_aggregation(graph, "How many invoices?")
    total = deterministic_aggregation(graph, "Sum the total amount across invoices")
    contract_total = deterministic_aggregation(graph, "What is the total contract value?")
    assert "8 records" in count[0].text
    assert "8 records" in punctuated[0].text
    assert "45000 USD" in total[0].text
    assert "450000 USD" in contract_total[0].text
    assert len(total[0].node_ids) == 8


def test_eval_dataset_has_twenty_cases_and_report_has_comparison_metrics(tmp_path):
    cases = load_dataset("eval/dataset.yaml")
    assert len(cases) >= 20
    build_sample(tmp_path / "kg")
    agent = BusinessQAAgent(FileGraphStore(tmp_path / "kg"), runs_dir=tmp_path / "runs")
    results = evaluate(agent, cases[:2], modes=("keyword", "graph"))
    report = render_report(results)
    assert "Recall@k" in report and "faithfulness" in report.lower()


def test_eval_report_marks_dense_mode_unavailable_without_index(tmp_path):
    build_sample(tmp_path / "kg")
    agent = BusinessQAAgent(FileGraphStore(tmp_path / "kg"), runs_dir=tmp_path / "runs")
    result = evaluate(agent, [{"question": "lunar mining policy", "gold_answer": "missing",
                              "gold_evidence_ids": []}], modes=("dense",))
    report = render_report(result)
    assert "not configured" in report


def test_fastapi_schema_ask_and_trace_routes(tmp_path):
    from fastapi.testclient import TestClient
    from kg_agent.api import create_app

    build_sample(tmp_path / "kg")
    agent = BusinessQAAgent(FileGraphStore(tmp_path / "kg"), runs_dir=tmp_path / "runs")
    client = TestClient(create_app(agent))
    assert client.get("/schema").json()["node_count"] == 40
    answer = client.post("/ask", json={"question": "How many invoices are in the graph?",
                                       "mode": "agent"})
    assert answer.status_code == 200
    run_id = answer.json()["trace"]["run_id"]
    assert client.get(f"/trace/{run_id}").status_code == 200


def test_aggregation_answer_verifies_without_retry_and_keeps_confidence(tmp_path):
    build_sample(tmp_path / "kg")
    agent = BusinessQAAgent(FileGraphStore(tmp_path / "kg"), runs_dir=tmp_path / "runs")
    response = agent.run("How many invoices?", mode="agent")
    assert "8 records" in response.answer
    assert response.confidence == 0.45
    assert len(response.trace["steps"]) == 2
