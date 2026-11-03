"""Plan, retrieve, answer, verify, and trace a bounded business Q&A request."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from kg_agent.models import AgentResponse, Evidence
from kg_agent.runtime.answerer import AnswerGenerator
from kg_agent.runtime.context_builder import build_context
from kg_agent.runtime.fusion import reciprocal_rank_fusion
from kg_agent.runtime.query_transformer import QueryTransformer
from kg_agent.runtime.rerank import EvidenceReranker
from kg_agent.runtime.tools.dense import PersistentDenseIndex
from kg_agent.runtime.tools.graph import (deterministic_aggregation,
                                          graph_global_search, graph_search)
from kg_agent.runtime.tools.keyword import keyword_search
from kg_agent.runtime.verifier import AnswerVerifier


class BusinessQAAgent:
    MODES = {"dense", "graph", "keyword", "hybrid", "agent"}

    def __init__(self, store, *, llm=None, embedder=None, dense_index=None,
                 system_prompt: str = "", runs_dir: Path | str = "runs"):
        self.store, self.llm, self.embedder = store, llm, embedder
        self.dense_index = dense_index
        self.transformer = QueryTransformer(store, llm=llm, embedder=embedder)
        self.answerer = AnswerGenerator(llm, system_prompt)
        self.reranker = EvidenceReranker(llm=llm)
        self.verifier = AnswerVerifier()
        self.runs_dir = Path(runs_dir)

    def _tools_for(self, mode, plan):
        if mode != "agent":
            return {"dense": ["dense"], "graph": ["graph"],
                    "keyword": ["keyword"], "hybrid": ["keyword", "dense", "graph"]}[mode]
        return list(plan.tools)

    def _retrieve(self, question, plan, tools, broaden=False):
        results = []
        if plan.intent == "aggregation" and "graph" in tools:
            aggregate = deterministic_aggregation(self.store, question)
            if aggregate:
                return [aggregate]
        if "keyword" in tools:
            results.append(keyword_search(self.store, question, limit=20))
        if "dense" in tools and self.dense_index:
            try:
                results.append(self.dense_index.search(question, limit=20))
            except FileNotFoundError:
                pass
        if "graph" in tools:
            graph_query = plan.graph_query
            if graph_query and graph_query.mode == "global":
                results.append(graph_global_search(self.store, question, limit=10,
                                                   embedder=self.embedder))
            else:
                seeds = graph_query.seeds[:] if graph_query else []
                if not seeds:
                    seeds = [entity.node_id for entity in plan.entities if entity.node_id]
                if not seeds:
                    seeds = [node_id for result_set in results for result in result_set
                             for node_id in result.node_ids][:5]
                if seeds:
                    hops = min(5, (graph_query.hops if graph_query else 2) + (1 if broaden else 0))
                    results.append(graph_search(self.store, seeds, hops=hops,
                        rel_types=graph_query.rel_types if graph_query else None,
                        limit=40 if broaden else 20))
        return results

    def run(self, question: str, *, mode: str = "hybrid", history=None,
            verbose: bool = False) -> AgentResponse:
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {sorted(self.MODES)}")
        trace = {"run_id": str(uuid.uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
                 "mode": mode, "steps": []}
        started = time.perf_counter()
        plan = self.transformer.transform(question, history=history)
        trace["steps"].append({"step": "query_transform", "intent": plan.intent,
                               "tools": plan.tools, "plan": plan.model_dump(mode="json")})
        selected_tools = self._tools_for(mode, plan)
        max_refinements = 2 if mode == "agent" else 0
        final = None
        final_evidence = []
        final_context = None
        for iteration in range(max_refinements + 1):
            iteration_started = time.perf_counter()
            tool_results = self._retrieve(plan.standalone_question, plan, selected_tools,
                                          broaden=iteration > 0)
            fused = reciprocal_rank_fusion(tool_results, limit=30)
            ranked = self.reranker.rerank(plan.standalone_question, fused, limit=12)
            context = build_context(ranked, relationship_question=plan.intent in
                                    {"relationship", "multi_hop", "comparison", "aggregation"})
            answer = self.answerer.answer(plan.standalone_question, context)
            verification = self.verifier.verify(plan.standalone_question, answer, context,
                                                plan.sub_questions)
            trace["steps"].append({"step": "retrieve_answer_verify", "iteration": iteration,
                "tools": selected_tools, "evidence_count": len(ranked),
                "citations": answer.citations, "verification": verification.model_dump(),
                "elapsed_ms": round((time.perf_counter() - iteration_started) * 1000, 2)})
            final, final_evidence, final_context = answer, ranked, context
            if verification.supported and verification.complete:
                break
            if iteration < max_refinements:
                selected_tools = list(dict.fromkeys([*selected_tools, "keyword", "dense", "graph"]))
        if final is None or final_context is None:
            raise RuntimeError("No answer was produced")
        if not self.verifier.verify(plan.standalone_question, final, final_context,
                                    plan.sub_questions).supported:
            if final_context.items:
                first = final_context.items[0]
                final.answer = f"The retrieved source says: {first.evidence.text}"
                final.citations = [first.citation_id]
                final.confidence = min(final.confidence, 0.4)
            else:
                final.answer = "I could not find supporting evidence in the knowledge graph."
                final.citations = []
                final.confidence = 0.0
        paths = []
        candidate_nodes = list(dict.fromkeys(node_id for item in final_evidence
                                             for node_id in item.node_ids))
        for index, left_id in enumerate(candidate_nodes):
            for right_id in candidate_nodes[index + 1:]:
                for path in self.store.find_paths(left_id, right_id, max_hops=4, k=1):
                    if len(path.edge_ids) > 1:
                        paths.append(path)
                        break
            if paths:
                break
        trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        trace["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        trace_path = self.runs_dir / f"{trace['run_id']}.json"
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        trace["trace_file"] = str(trace_path)
        if verbose:
            print(json.dumps(trace, ensure_ascii=False, indent=2))
        return AgentResponse(answer=final.answer, citations=final.citations,
            evidence=final_evidence, graph_paths=paths, confidence=final.confidence,
            query_plan=plan, trace=trace)
