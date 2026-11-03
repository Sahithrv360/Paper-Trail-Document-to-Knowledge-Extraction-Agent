"""Question normalization, entity linking, intent selection, and tool routing."""

import re

from kg_agent.models import GraphQuery, QueryPlan, SubQuestion
from kg_agent.runtime.entity_linker import link_entity


GRAPH_TERMS = ("connected", "relationship", "related", "between", "path", "link",
               "compare", "comparison", "how many", "total", "sum", "count", "all ")
DENSE_TERMS = ("explain", "describe", "what does", "policy", "clause", "meaning")
GLOBAL_TERMS = ("overall", "across all", "main themes", "trend", "summarize everything")


class QueryTransformer:
    def __init__(self, store, *, llm=None, embedder=None, business_context=None):
        self.store, self.llm, self.embedder = store, llm, embedder
        self.business_context = business_context

    def _expand_glossary(self, question):
        glossary = getattr(self.business_context, "glossary", {}) or {}
        output = question
        for acronym, expansion in glossary.items():
            output = re.sub(rf"(?<![\w-]){re.escape(acronym)}(?![\w-])",
                            f"{acronym} ({expansion})", output, flags=re.IGNORECASE)
        return output

    def _standalone(self, question, history):
        if not history:
            return question
        previous = " ".join(str(item.get("content", "")) for item in history[-4:]
                            if isinstance(item, dict))
        followup = re.match(r"\s*(which others|what about them|and those|how about those)\b",
                            question, flags=re.IGNORECASE)
        if followup and previous:
            return f"Regarding {previous[-240:]}, {question.strip()}"
        return question

    def _rule_plan(self, question):
        lower = question.casefold()
        if any(term in lower for term in GLOBAL_TERMS):
            intent, tools, mode = "summary_global", ["graph", "dense"], "global"
        elif any(term in lower for term in ("how many", "count", "sum", "total")):
            intent, tools, mode = "aggregation", ["graph", "keyword"], "local"
        elif any(term in lower for term in ("compare", "comparison", "versus", " vs ")):
            intent, tools, mode = "comparison", ["graph", "dense", "keyword"], "local"
        elif any(term in lower for term in GRAPH_TERMS):
            intent, tools, mode = "relationship", ["graph", "keyword"], "local"
        elif any(term in lower for term in DENSE_TERMS):
            intent, tools, mode = "definition", ["dense", "keyword"], "local"
        else:
            intent, tools, mode = "lookup", ["keyword", "dense"], "local"
        return intent, tools, mode

    def transform(self, question: str, history: list[dict[str, str]] | None = None) -> QueryPlan:
        if not question.strip():
            raise ValueError("question cannot be empty")
        question = self._expand_glossary(self._standalone(question, history or []))
        schema = self.store.schema()
        linked = []
        # Exact entity names and aliases are linked in code before a planner can invent IDs.
        for node in self.store.all_nodes():
            for mention in [node.name, *node.aliases, node.id]:
                if mention.casefold() in question.casefold():
                    linked.append(link_entity(self.store, mention, embedder=self.embedder))
        # Link likely proper-name phrases and document IDs, including minor misspellings.
        mentions = re.findall(r"\b[A-Z0-9][\w.-]*(?:\s+[A-Z0-9][\w.-]*){1,4}\b|"
                              r"\b[A-Za-z][\w.-]*-[A-Za-z0-9][\w.-]*\b", question)
        for mention in mentions:
            cleaned = re.sub(r"^(Which|What|Who|When|Where|How|Show|List|Tell)\s+", "",
                             mention, flags=re.IGNORECASE).strip()
            if cleaned and cleaned.casefold() not in {item.mention.casefold() for item in linked}:
                candidate = link_entity(self.store, cleaned, embedder=self.embedder)
                if candidate.node_id or (candidate.candidates and candidate.candidates[0].score >= 0.6):
                    linked.append(candidate)
        intent, tools, graph_mode = self._rule_plan(question)
        plan = None
        if self.llm:
            prompt = ("Plan retrieval for this business question. Return JSON matching the "
                "QueryPlan schema. Use only these entity types and relation types. "
                f"Entity types: {[item.type for item in schema.entity_types]}. "
                f"Relation types: {[item.type for item in schema.relation_types]}. "
                f"Business glossary: {getattr(self.business_context, 'glossary', {})}. "
                "For graph questions use graph; exact IDs use keyword; descriptions use dense. "
                f"Question: {question}")
            try:
                plan = QueryPlan.model_validate_json(self.llm.complete(
                    [{"role": "user", "content": prompt}], json_mode=True,
                    response_model=QueryPlan))
            except Exception:
                plan = None
        if plan is None:
            seeds = list(dict.fromkeys(item.node_id for item in linked if item.node_id))
            plan = QueryPlan(original=question, standalone_question=question, intent=intent,
                entities=linked, keyword_query=question, dense_query=question,
                graph_query=GraphQuery(seeds=seeds, mode=graph_mode) if "graph" in tools else None,
                tools=tools, rationale="Deterministic intent rules and code-based entity linking.")
        else:
            # Enforce routing overrides and schema allowlists after model planning.
            allowed_relations = {item.type for item in schema.relation_types}
            allowed_node_ids = {node.id for node in self.store.all_nodes()}
            if plan.graph_query and plan.graph_query.rel_types is not None:
                plan.graph_query.rel_types = [rel for rel in plan.graph_query.rel_types
                                              if rel in allowed_relations]
            if plan.graph_query:
                plan.graph_query.seeds = [seed for seed in plan.graph_query.seeds
                                          if seed in allowed_node_ids]
            if intent in {"relationship", "multi_hop", "aggregation", "comparison",
                          "summary_global"}:
                plan.intent = intent
                plan.tools = list(dict.fromkeys([*tools, *plan.tools]))
            resolved = []
            for entity in plan.entities:
                actual = link_entity(self.store, entity.mention, embedder=self.embedder)
                resolved.append(actual)
            plan.entities = list({item.mention.casefold(): item
                                  for item in [*resolved, *linked]}.values())
            plan.original = question
            plan.standalone_question = plan.standalone_question or question
        if plan.intent in {"multi_hop", "comparison", "aggregation"} and not plan.sub_questions:
            plan.sub_questions = [SubQuestion(question=plan.standalone_question,
                                               tools=plan.tools)]
        return plan
