"""Plan, retrieve, answer, verify, and trace a bounded business Q&A request."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import time
import uuid

from kg_agent.models import AgentResponse, Evidence
from kg_agent.runtime.answerer import AnswerGenerator
from kg_agent.runtime.context_builder import build_context
from kg_agent.runtime.fusion import reciprocal_rank_fusion
from kg_agent.runtime.query_transformer import QueryTransformer
from kg_agent.runtime.rerank import EvidenceReranker
from kg_agent.runtime.scoping import scope_evidence_to_question
from kg_agent.runtime.tools.dense import PersistentDenseIndex
from kg_agent.runtime.tools.graph import (deterministic_aggregation,
                                          graph_global_search, graph_search)
from kg_agent.runtime.tools.keyword import keyword_search
from kg_agent.runtime.verifier import AnswerVerifier

logger = logging.getLogger(__name__)


class BusinessQAAgent:
    MODES = {"dense", "graph", "keyword", "hybrid", "agent"}

    def __init__(self, store, *, llm=None, embedder=None, dense_index=None,
                 system_prompt: str = "", runs_dir: Path | str = "runs",
                 context_token_budget: int = 6000):
        self.store, self.llm, self.embedder = store, llm, embedder
        self.dense_index = dense_index
        self.transformer = QueryTransformer(store, llm=llm, embedder=embedder)
        self.answerer = AnswerGenerator(llm, system_prompt)
        self.reranker = EvidenceReranker(llm=llm)
        self.verifier = AnswerVerifier()
        self.runs_dir = Path(runs_dir)
        self.context_token_budget = context_token_budget

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
                        limit=40 if broaden or plan.intent == "comparison" else 20))
        return results

    def run(self, question: str, *, mode: str = "hybrid", history=None,
            verbose: bool = False, progress_callback=None) -> AgentResponse:
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {sorted(self.MODES)}")
        trace = {"run_id": str(uuid.uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
                 "mode": mode, "steps": []}
        def emit_progress(step):
            if progress_callback:
                progress_callback({"stage": step.get("step"), "event": step})

        started = time.perf_counter()
        plan = self.transformer.transform(question, history=history)
        trace["steps"].append({"step": "query_transform", "intent": plan.intent,
                               "tools": plan.tools, "plan": plan.model_dump(mode="json")})
        emit_progress(trace["steps"][-1])
        # Ignore weak fuzzy candidates: ordinary capitalized phrases in a natural
        # question should not block retrieval for a separate, clearly resolved customer.
        exact_mentions = [set(re.findall(r"[a-z0-9]+", entity.mention.casefold()))
                          for entity in plan.entities if entity.node_id]
        unresolved = [entity for entity in plan.entities if not entity.node_id and entity.candidates
                      and entity.candidates[0].score >= 0.72
                      and not any(tokens and tokens.issubset(
                          set(re.findall(r"[a-z0-9]+", entity.mention.casefold())))
                          for tokens in exact_mentions)]
        if unresolved:
            entity = unresolved[0]
            closest = ", ".join(f"{candidate.name} ({candidate.score:.0%})"
                                 for candidate in entity.candidates[:3])
            answer_text = (f"I couldn't find an exact match for {entity.mention!r} in the "
                "selected knowledge graph, so I did not use other records as a substitute. "
                f"Closest name matches: {closest}. Check the spelling or review status, "
                "or enable unreviewed records if appropriate.")
            plan.needs_clarification = answer_text
            trace["steps"].append({"step": "entity_resolution", "status": "needs_clarification",
                                   "mention": entity.mention,
                                   "candidate_count": len(entity.candidates)})
            trace["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
            trace["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            trace_path = self.runs_dir / f"{trace['run_id']}.json"
            trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
            trace["trace_file"] = str(trace_path)
            emit_progress(trace["steps"][-1])
            return AgentResponse(answer=answer_text, confidence=0.0, query_plan=plan,
                                 trace=trace)
        if (plan.intent == "comparison" and plan.graph_query
                and re.search(r"\b(?:another|other)\s+(?:customer|client|account|vendor)\b",
                              question, re.IGNORECASE)
                and sum(1 for entity in plan.entities if entity.node_id) == 1):
            # If the user names one customer and asks for another, select a second
            # reviewed customer with invoice activity so comparison retrieval has
            # evidence for both sides, even when they are in disconnected graph parts.
            nodes = {node.id: node for node in self.store.all_nodes()}
            named_customer_ids = {entity.node_id for entity in plan.entities
                                  if entity.node_id in nodes and nodes[entity.node_id].type == "Customer"}
            if len(named_customer_ids) == 1:
                invoice_counts = {}
                for edge in getattr(self.store, "edges", []):
                    if edge.type != "BILLED_TO":
                        continue
                    left, right = nodes.get(edge.source), nodes.get(edge.target)
                    if left and right and left.type == "Customer" and right.type == "Invoice":
                        invoice_counts[left.id] = invoice_counts.get(left.id, 0) + 1
                    elif left and right and right.type == "Customer" and left.type == "Invoice":
                        invoice_counts[right.id] = invoice_counts.get(right.id, 0) + 1
                peers = [node for node in nodes.values() if node.type == "Customer"
                         and node.id not in named_customer_ids and invoice_counts.get(node.id, 0) > 0]
                peers.sort(key=lambda node: (-invoice_counts[node.id], node.name.casefold(), node.id))
                if peers:
                    peer = peers[0]
                    plan.graph_query.seeds = list(dict.fromkeys(
                        [*plan.graph_query.seeds, peer.id]))
                    plan.standalone_question += f" Selected comparison peer: {peer.name}."
                    plan.keyword_query = plan.standalone_question
                    plan.dense_query = plan.standalone_question
                    trace["steps"].append({"step": "comparison_peer_selection",
                        "status": "selected", "entity": peer.name,
                        "invoice_count": invoice_counts[peer.id],
                        "selection_rule": "highest reviewed invoice count; alphabetical tie-break"})
                    emit_progress(trace["steps"][-1])
        selected_tools = self._tools_for(mode, plan)
        max_refinements = 2 if mode == "agent" else 0
        final = None
        final_evidence = []
        final_context = None
        for iteration in range(max_refinements + 1):
            iteration_started = time.perf_counter()
            tool_results = self._retrieve(plan.standalone_question, plan, selected_tools,
                                          broaden=iteration > 0)
            trace["steps"].append({"step": "retrieval", "iteration": iteration,
                "tools": selected_tools, "result_sets": len(tool_results),
                "evidence_candidates": sum(len(result) for result in tool_results)})
            emit_progress(trace["steps"][-1])
            fused = reciprocal_rank_fusion(tool_results, limit=30)
            trace["steps"].append({"step": "rank_fusion", "iteration": iteration,
                "candidate_count": len(fused), "method": "reciprocal_rank_fusion"})
            emit_progress(trace["steps"][-1])
            fused, scope = scope_evidence_to_question(plan.standalone_question, self.store, fused)
            if scope:
                trace["steps"].append({"step": "evidence_scope", "iteration": iteration,
                    "scope_type": scope["type"], "entity": scope["name"],
                    "evidence_count": len(fused)})
                emit_progress(trace["steps"][-1])
            ranked = self.reranker.rerank(plan.standalone_question, fused, limit=12)
            trace["steps"].append({"step": "evidence_reranking", "iteration": iteration,
                "evidence_count": len(ranked), "limit": 12})
            emit_progress(trace["steps"][-1])
            context = build_context(ranked, relationship_question=plan.intent in
                                    {"relationship", "multi_hop", "comparison", "aggregation"},
                                    token_budget=self.context_token_budget)
            trace["steps"].append({"step": "evidence_packet", "iteration": iteration,
                "evidence_count": len(context.items), "token_estimate": context.token_estimate,
                "citation_ids": [item.citation_id for item in context.items]})
            emit_progress(trace["steps"][-1])
            answer = self.answerer.answer(plan.standalone_question, context)
            trace["steps"].append({"step": "answer_generation", "iteration": iteration,
                "citations": answer.citations, "confidence": answer.confidence,
                "fallback_reason": answer.reasoning_summary if answer.confidence <= 0.35 else None})
            emit_progress(trace["steps"][-1])
            verification = self.verifier.verify(plan.standalone_question, answer, context,
                                                plan.sub_questions)
            trace["steps"].append({"step": "answer_verification", "iteration": iteration,
                **verification.model_dump()})
            emit_progress(trace["steps"][-1])
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
            logger.warning("Final answer failed evidence verification; returning safe prose")
            synthetic_note = ("Note: Some retrieved records are labelled synthetic/demo data.\n\n"
                              if final_context.source_metadata.get("has_synthetic") else "")
            final.answer = (synthetic_note + "I couldn't verify a complete answer from the available records.\n"
                            "Citations couldn't be attached to this answer.")
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
        cited_evidence = [item.evidence.model_copy(update={"citation_id": item.citation_id})
                          for item in final_context.items
                          if item.citation_id in set(final.citations)]
        return AgentResponse(answer=final.answer, citations=final.citations,
            evidence=cited_evidence, graph_paths=paths, confidence=final.confidence,
            query_plan=plan, trace=trace)
