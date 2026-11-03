"""Embedding providers with an optional local sentence-transformers fallback."""

from .config import AgentConfig


class OllamaEmbeddings:
    def __init__(self, config: AgentConfig):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install kg-agent dependencies to use embeddings") from exc
        self.model = config.embed_model
        self.client = OpenAI(base_url=config.ollama_base_url, api_key="ollama",
                             timeout=config.request_timeout_seconds, max_retries=0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = self.client.embeddings.create(model=self.model, input=texts)
        return [row.embedding for row in sorted(result.data, key=lambda row: row.index)]


class SentenceTransformerEmbeddings:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("Install sentence-transformers to enable embedding fallback") from exc
        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self.model.encode(texts, normalize_embeddings=True).tolist()


class FallbackEmbeddings:
    """Try Ollama first, then lazily initialize the optional local fallback."""

    def __init__(self, primary, fallback_model: str):
        self.primary = primary
        self.fallback_model = fallback_model
        self.fallback = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return self.primary.embed(texts)
        except Exception:
            if self.fallback is None:
                self.fallback = SentenceTransformerEmbeddings(self.fallback_model)
            return self.fallback.embed(texts)


def create_embeddings(config: AgentConfig):
    if config.embedding_provider == "ollama":
        return FallbackEmbeddings(OllamaEmbeddings(config), config.embedding_fallback_model)
    if config.embedding_provider == "sentence-transformers":
        return SentenceTransformerEmbeddings(config.embedding_fallback_model)
    raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {config.embedding_provider}")
