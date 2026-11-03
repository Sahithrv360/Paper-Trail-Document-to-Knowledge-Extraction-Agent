"""Environment and YAML configuration for kg-agent."""

from pathlib import Path
import os

from pydantic import BaseModel, ConfigDict, Field


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    llm_provider: str = "ollama"
    llm_model: str = "qwen2.5:7b-instruct"
    ollama_base_url: str = "http://localhost:11434/v1"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str | None = None
    embed_model: str = "nomic-embed-text"
    embedding_provider: str = "ollama"
    embedding_fallback_model: str = "BAAI/bge-small-en-v1.5"
    request_timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_retries: int = Field(default=1, ge=0, le=3)

    @classmethod
    def load(cls, config_path: Path | None = None, env_path: Path | None = None):
        """Load optional dotenv/YAML values, with process environment taking priority."""
        env_file = Path(env_path) if env_path else Path(".env")
        if env_file.is_file():
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

        values = {}
        yaml_path = Path(config_path) if config_path else Path("config.yaml")
        if yaml_path.is_file():
            try:
                import yaml
            except ImportError as exc:
                raise RuntimeError("Install PyYAML to read config.yaml") from exc
            loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError("config.yaml must contain a mapping")
            values.update(loaded)

        env_names = {
            "llm_provider": "LLM_PROVIDER", "llm_model": "LLM_MODEL",
            "ollama_base_url": "OLLAMA_BASE_URL",
            "openrouter_base_url": "OPENROUTER_BASE_URL",
            "openrouter_api_key": "OPENROUTER_API_KEY",
            "embed_model": "EMBED_MODEL", "embedding_provider": "EMBEDDING_PROVIDER",
            "embedding_fallback_model": "EMBEDDING_FALLBACK_MODEL",
            "request_timeout_seconds": "REQUEST_TIMEOUT_SECONDS",
            "max_retries": "MAX_RETRIES",
        }
        for field, name in env_names.items():
            if name in os.environ:
                values[field] = os.environ[name]
        return cls.model_validate(values)

    @property
    def llm_base_url(self) -> str:
        if self.llm_provider == "ollama":
            return self.ollama_base_url
        if self.llm_provider == "openrouter":
            return self.openrouter_base_url
        raise ValueError(f"Unsupported LLM_PROVIDER: {self.llm_provider}")

    @property
    def api_key(self) -> str:
        if self.llm_provider == "openrouter":
            if not self.openrouter_api_key:
                raise ValueError("OPENROUTER_API_KEY is required for OpenRouter")
            return self.openrouter_api_key
        return "ollama"
