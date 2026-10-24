"""Configuration for the folder ingestion service and optional extraction.

Module-level constants keep the original interface. ``Settings.from_env()`` reads the
same environment at call time (so tests can pass their own mapping) and adds the
resource limits, OCR selection and the remote-extraction opt-in.

Nothing here enables a hosted call by default: remote extraction requires
``PAPERTRAIL_ALLOW_REMOTE`` set to the exact acceptance phrase below *and* an API key
*and* a configured model ID.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


def load_env_file(path=".env") -> int:
    """Load KEY=VALUE lines from a local .env without overriding real environment values.

    Minimal and dependency-free: blank lines and # comments are skipped, surrounding
    quotes are removed. Set PAPERTRAIL_NO_DOTENV=1 to disable. Returns keys loaded.
    """
    if os.environ.get("PAPERTRAIL_NO_DOTENV"):
        return 0
    env_path = Path(path)
    if not env_path.is_file():
        return 0
    loaded = 0
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ and value != "":
            os.environ[key] = value
            loaded += 1
    return loaded


load_env_file()

WATCH_FOLDER = Path(os.getenv("WATCH_FOLDER", "./watched")).expanduser().resolve()
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "./paper_trail.sqlite3")).expanduser().resolve()
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "2"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_MB", "25")) * 1024 * 1024
SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}
GRAPH_PATH = Path(os.getenv("GRAPH_PATH", "./paper_trail_graph.sqlite3")).expanduser().resolve()
# "none" keeps the original queue-only behaviour. Other values opt in explicitly.
EXTRACTION_PROVIDER = os.getenv("EXTRACTION_PROVIDER", "none").lower()
EXTRACTION_DEVICE = os.getenv("EXTRACTION_DEVICE", "cpu")

PROVIDERS = ("none", "donut", "ocr", "vision-api")
OCR_ENGINES = ("none", "tesseract", "rapidocr")
REMOTE_ACCEPTANCE_PHRASE = "I_ACCEPT_DOCUMENTS_LEAVE_THIS_DEVICE"


def _int(env, name, default):
    value = env.get(name)
    return int(value) if value not in (None, "") else default


def _float(env, name, default):
    value = env.get(name)
    return float(value) if value not in (None, "") else default


def _optional_float(env, name) -> Optional[float]:
    value = env.get(name)
    return float(value) if value not in (None, "") else None


@dataclass(frozen=True)
class Settings:
    provider: str = "none"
    device: str = "cpu"
    # Parser/OCR used to produce evidence text. "ocr" provider requires an engine.
    parser_engine: str = "none"
    tesseract_cmd: str = "tesseract"
    ocr_language: str = "eng"
    ocr_timeout_seconds: float = 120.0
    # Resource limits (one worker, one page at a time).
    max_pages: int = 5
    max_page_pixels: int = 9_000_000      # ~A4 at 300 dpi; larger pages are downscaled
    pdf_render_dpi: int = 300           # capped by max_page_pixels
    # Remote extraction (disabled unless every condition holds).
    allow_remote: str = ""
    vision_backend: str = "anthropic"
    vision_model: str = ""
    vision_api_key: str = ""
    vision_api_base_url: str = "https://api.anthropic.com"
    vision_timeout_seconds: float = 90.0
    vision_max_retries: int = 2
    vision_max_output_tokens: int = 4096
    vision_max_request_bytes: int = 8 * 1024 * 1024
    vision_max_image_pixels: int = 2_500_000
    vision_send_parser_text: bool = True
    price_input_per_mtok: Optional[float] = None    # only for a local cost estimate
    price_output_per_mtok: Optional[float] = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = None) -> "Settings":
        env = os.environ if env is None else env
        provider = env.get("EXTRACTION_PROVIDER", "none").strip().lower()
        parser = env.get("PARSER_ENGINE", "").strip().lower()
        if not parser:
            parser = "tesseract" if provider == "ocr" else "none"
        backend = env.get("VISION_API_BACKEND", "anthropic").strip().lower()
        return cls(
            provider=provider,
            device=env.get("EXTRACTION_DEVICE", "cpu"),
            parser_engine=parser,
            tesseract_cmd=env.get("TESSERACT_CMD", "tesseract"),
            ocr_language=env.get("OCR_LANGUAGE", "eng"),
            ocr_timeout_seconds=_float(env, "OCR_TIMEOUT_SECONDS", 120.0),
            max_pages=_int(env, "MAX_PAGES", 5),
            max_page_pixels=_int(env, "MAX_PAGE_PIXELS", 9_000_000),
            pdf_render_dpi=_int(env, "PDF_RENDER_DPI", 300),
            allow_remote=env.get("PAPERTRAIL_ALLOW_REMOTE", ""),
            vision_backend=backend,
            vision_model=env.get("VISION_API_MODEL", "").strip(),
            vision_api_key=env.get("VISION_API_KEY", "") or env.get("ANTHROPIC_API_KEY", ""),
            vision_api_base_url=env.get("VISION_API_BASE_URL", "https://api.anthropic.com"),
            vision_timeout_seconds=_float(env, "VISION_API_TIMEOUT_SECONDS", 90.0),
            vision_max_retries=_int(env, "VISION_API_MAX_RETRIES", 2),
            vision_max_output_tokens=_int(env, "VISION_API_MAX_OUTPUT_TOKENS", 4096),
            vision_max_request_bytes=int(_float(env, "VISION_API_MAX_REQUEST_MB", 8) * 1024 * 1024),
            vision_max_image_pixels=_int(env, "VISION_API_MAX_IMAGE_PIXELS", 2_500_000),
            vision_send_parser_text=env.get("VISION_API_SEND_PARSER_TEXT", "1") not in ("0", "false", "no"),
            price_input_per_mtok=_optional_float(env, "VISION_API_PRICE_INPUT_PER_MTOK"),
            price_output_per_mtok=_optional_float(env, "VISION_API_PRICE_OUTPUT_PER_MTOK"),
        )

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(f"EXTRACTION_PROVIDER must be one of {PROVIDERS}, not {self.provider!r}")
        if self.parser_engine not in OCR_ENGINES:
            raise ValueError(f"PARSER_ENGINE must be one of {OCR_ENGINES}, not {self.parser_engine!r}")
        if self.provider == "ocr" and self.parser_engine == "none":
            raise ValueError("EXTRACTION_PROVIDER=ocr needs PARSER_ENGINE=tesseract or rapidocr")
        if self.max_pages < 1 or self.max_page_pixels < 100_000:
            raise ValueError("MAX_PAGES must be >= 1 and MAX_PAGE_PIXELS >= 100000")

    def remote_blockers(self) -> list:
        """Every unmet condition for sending a document to a hosted API (empty = allowed)."""
        blockers = []
        if self.allow_remote != REMOTE_ACCEPTANCE_PHRASE:
            blockers.append(f"set PAPERTRAIL_ALLOW_REMOTE={REMOTE_ACCEPTANCE_PHRASE} to accept "
                            "that document images and text leave this device")
        if not self.vision_api_key:
            blockers.append("set VISION_API_KEY (or ANTHROPIC_API_KEY)")
        if not self.vision_model:
            blockers.append("set VISION_API_MODEL to a model ID from the vendor's current docs")
        if self.vision_backend != "anthropic":
            blockers.append(f"unsupported VISION_API_BACKEND {self.vision_backend!r}")
        return blockers
