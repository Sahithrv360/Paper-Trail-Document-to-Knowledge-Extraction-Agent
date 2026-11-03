"""Generate versioned, schema-grounded business system prompts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field

from kg_agent.models import GraphSchema, SystemPromptArtifact


class BusinessExample(BaseModel):
    question: str
    plan_hint: str = ""
    ideal_answer: str = ""


class BusinessContext(BaseModel):
    domain: str
    audience: str = "Business users"
    goals: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    kpis: list[str] = Field(default_factory=list)
    answer_style: str = "Concise and evidence based"
    examples: list[BusinessExample] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path | str):
        import yaml
        content = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(content)


class PromptRefinement(BaseModel):
    prompt: str
    entity_types: list[str]
    relation_types: list[str]


def _template_environment() -> Environment:
    template_dir = Path(__file__).resolve().parents[1] / "templates"
    return Environment(loader=FileSystemLoader(template_dir),
                       undefined=StrictUndefined, autoescape=False,
                       trim_blocks=True, lstrip_blocks=True)


def _valid_refinement(text: str, schema: GraphSchema) -> bool:
    """Reject refinement if it adds an entity or relation type not in the KG."""
    known = {item.type for item in schema.entity_types}
    known.update(item.type for item in schema.relation_types)
    # Detect type names in common KG notation: (Entity), -[RELATION]->.
    import re
    mentioned = set(re.findall(r"\(([A-Za-z][A-Za-z0-9_]*)\)|\[([A-Z][A-Z0-9_]*)\]", text))
    names = {part for pair in mentioned for part in pair if part}
    return names <= known


def generate_system_prompt(schema: GraphSchema, business_context: BusinessContext,
                           *, refine: bool = False, llm=None,
                           artifacts_dir: Path | str = "artifacts") -> SystemPromptArtifact:
    env = _template_environment()
    template = env.get_template("system_prompt.j2")
    prompt = template.render(schema=schema, context=business_context)
    refined = False
    model_name = None
    if refine:
        if llm is None:
            raise ValueError("An LLM instance is required when --refine is enabled")
        try:
            raw_response = llm.complete([
                {"role": "system", "content": "Improve clarity and domain-specific query guidance. "
                 "Do not introduce entity or relation types beyond the supplied schema. Return JSON "
                 "with prompt, entity_types, and relation_types. The type arrays must list every "
                 "type mentioned in the prompt and must exactly match the supplied schema."},
                {"role": "user", "content": json.dumps({"prompt": prompt,
                    "entity_types": [item.type for item in schema.entity_types],
                    "relation_types": [item.type for item in schema.relation_types]}, ensure_ascii=False)},
            ], json_mode=True, response_model=PromptRefinement)
            response = PromptRefinement.model_validate_json(raw_response)
            expected_entities = {item.type for item in schema.entity_types}
            expected_relations = {item.type for item in schema.relation_types}
            if (set(response.entity_types) == expected_entities and
                    set(response.relation_types) == expected_relations and
                    _valid_refinement(response.prompt, schema)):
                prompt, refined = response.prompt, True
        except Exception:
            pass
        model_name = getattr(getattr(llm, "config", None), "llm_model", None)

    schema_payload = schema.model_dump(mode="json")
    schema_hash = hashlib.sha256(json.dumps(schema_payload, sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()
    output_dir = Path(artifacts_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    versions = []
    for path in output_dir.glob("system_prompt.v*.md"):
        try:
            versions.append(int(path.stem.split(".v", 1)[1]))
        except (ValueError, IndexError):
            continue
    version = max(versions, default=0) + 1
    created = datetime.now(timezone.utc).isoformat()
    artifact = SystemPromptArtifact(prompt=prompt, version=version,
                                    schema_hash=schema_hash, created_at=created,
                                    model=model_name, refined=refined)
    md_path = output_dir / f"system_prompt.v{version}.md"
    md_path.write_text(prompt.rstrip() + "\n", encoding="utf-8")
    md_path.with_suffix(".json").write_text(
        artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return artifact
