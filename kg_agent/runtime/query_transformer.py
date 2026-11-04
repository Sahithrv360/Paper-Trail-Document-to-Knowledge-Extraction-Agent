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
        recent = [item for item in history[-6:] if isinstance(item, dict)
                  and item.get("role") in {"user", "assistant"} and item.get("content")]
        previous = "\n".join(
            f"{item['role']}: {str(item['content'])[-700:]}" for item in recent
        )[-2400:]
        followup = re.match(
            r"\s*(and\b|also\b|what about\b|how about\b|which (?:one|ones|others)\b|"
            r"what happened\b|who asked\b|any (?:update|news)\b|did (?:they|it|we)\b|"
            r"has (?:it|they|that)\b|have (?:they|we)\b|was (?:it|that)\b|"
            r"when (?:will|was|did)\b|who (?:is|was|are)\b|why (?:is|was|did)\b|"
            r"can (?:they|we|it)\b|is (?:it|that)\b|what is the status\b|"
            r"what's the status\b)", question, flags=re.IGNORECASE)
        if followup and previous:
            return (f"Conversation context (use it to resolve omitted customer, invoice, or entity names):\n"
                    f"{previous}\nFollow-up question: {question.strip()}")
        return question

    def _rule_plan(self, question):
        lower = question.casefold()
        if any(term in lower for term in GLOBAL_TERMS):
            intent, tools, mode = "summary_global", ["graph", "dense"], "global"
        elif (re.search(r"\b(?:who|which)\b.*\b(?:bought|buy|buys|buying|purchase|purchases|purchased|purchasing|ordered|orders|sold|sell|sells|selling)\b", lower)
              or re.search(r"\b(?:vendor|seller)\b.*\b(?:sold|sell|sells|selling)\b", lower)):
            intent, tools, mode = "relationship", ["graph", "keyword"], "local"
        elif re.search(r"\b(how many|count|sum|total|number of)\b", lower):
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
            cleaned = re.sub(r"^(Which|What|Who|When|Where|How|Compare|Prepare|Summarize|"
                             r"Summary|Analyze|Review|Find|Give|Show|List|Tell|Explain|"
                             r"Describe|Search|Check|Provide|For)\s+", "",
                             mention, flags=re.IGNORECASE).strip()
            # Sentence punctuation can make the regex absorb the next capitalized
            # question word (for example, "10060256. What"). Keep entity mentions
            # free of terminal punctuation and trailing question glue words.
            cleaned = re.sub(r"[.,!?;:]+$", "", cleaned).strip()
            words = cleaned.split()
            while words and words[-1].casefold() in {
                    "what", "who", "when", "where", "which", "how", "and", "or",
                    "for", "about", "including", "then", "also"}:
                words.pop()
            cleaned = " ".join(words)
            # A sentence boundary can make a known hyphenated customer name look
            # like a longer, unresolved entity (for example, "Wells-Carlson. Summarize").
            # Exact graph matches were linked above, so don't fuzzy-resolve a phrase
            # that already contains one of those known entity names.
            if any(re.search(r"(?<!\w)" + re.escape(item.mention) + r"(?!\w)",
                             cleaned, flags=re.IGNORECASE) for item in linked):
                continue
            if cleaned and cleaned.casefold() not in {item.mention.casefold() for item in linked}:
                candidate = link_entity(self.store, cleaned, embedder=self.embedder)
                if candidate.node_id or (candidate.candidates and candidate.candidates[0].score >= 0.6):
                    linked.append(candidate)
        intent, tools, graph_mode = self._rule_plan(question)
        if linked and any(term in question.casefold() for term in
                          ("summary", "summarize", "overview", "business history")):
            intent, tools, graph_mode = "multi_hop", ["graph", "keyword", "dense"], "local"
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
