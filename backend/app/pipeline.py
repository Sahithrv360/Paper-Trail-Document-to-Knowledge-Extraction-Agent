"""Connects the registry's queued jobs to extraction and graph synchronization.

Phase contract: every extraction is stored with review_status="needs_review". Nothing
is auto-approved, whatever the checks report; findings are recorded for the reviewer.
Model output is untrusted until it passes schema validation, and the graph is only
touched after validation succeeds, so a failed job leaves the last valid graph intact.

Per job: load the document (bounded pages/pixels) -> optional parser/OCR (evidence
text and boxes) -> provider -> validation -> ledger/graph sync. One job at a time.
"""

from pathlib import Path

from .extraction.document_input import (DocumentLoadError, IMAGE_SUFFIXES, PDF_SUFFIXES,
                                        load_document)
from .extraction.local_ocr import OcrUnavailable
from .extraction.providers import InvalidModelResponse, extract_validated
from .graph import GraphStore, ReviewedExtractionConflict
from .ingestion.registry import DocumentRegistry

REVIEW_STATUS = "needs_review"
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | PDF_SUFFIXES


def load_image(path: Path):
    from PIL import Image  # optional dependency, imported lazily

    with Image.open(path) as opened:
        return opened.convert("RGB")


class _PreloadedPage:
    def __init__(self, number, image):
        self.number, self._image, self.source = number, image, "image"
        self.width = self.height = None
        self.image_png, self.text_fragments, self.scale = None, [], 1.0

    def image(self):
        return self._image


class _PreloadedDocument:
    """Wraps an ``image_loader`` result (compatibility with the original interface)."""
    kind = "image"

    def __init__(self, path, image):
        self.path, self.pages, self.page_count_in_file = Path(path), [_PreloadedPage(1, image)], 1

    def first_image(self):
        return self.pages[0].image()

    def summary(self):
        return {"kind": "image", "pages": [{"page": 1, "source": "image"}]}


def _permanent_error_types():
    """Errors that retrying cannot fix."""
    from .extraction.vision_api import RemoteExtractionDisabled, RemoteRequestRejected
    return (DocumentLoadError, OcrUnavailable, RemoteExtractionDisabled, RemoteRequestRejected)


class JobProcessor:
    def __init__(self, registry: DocumentRegistry, graph: GraphStore, provider,
                 image_loader=None, max_attempts: int = 3, document_loader=None,
                 parser=None, settings=None):
        self.registry = registry
        self.graph = graph
        self.provider = provider
        self.max_attempts = max_attempts
        self.parser = parser
        self.settings = settings
        if document_loader is not None:
            self.document_loader = document_loader
        elif image_loader is not None:
            self.document_loader = self._compat_loader(image_loader)
        else:
            self.document_loader = self._default_loader

    def _default_loader(self, path):
        settings = self.settings
        if settings is None:
            return load_document(path)
        return load_document(path, settings.max_pages, settings.max_page_pixels,
                             settings.pdf_render_dpi)

    def _compat_loader(self, image_loader):
        def loader(path):
            if Path(path).suffix.lower() in PDF_SUFFIXES:
                return self._default_loader(path)
            return _PreloadedDocument(path, image_loader(path))
        return loader

    def process_next(self):
        """Claim and run one job. Returns a result dict, or None when the queue is empty."""
        job = self.registry.claim_next_job()
        if job is None:
            return None
        outcome = {"job_id": job["job_id"], "document_id": job["document_id"],
                   "action": job["action"]}
        try:
            outcome["outcome"] = (self._delete(job) if job["action"] == "delete"
                                  else self._process(job))
            self.registry.complete_job(job["job_id"])
        except InvalidModelResponse as exc:
            status = self.registry.fail_job(job["job_id"], str(exc), self.max_attempts)
            outcome.update(outcome="retry" if status == "queued" else "failed",
                           error=str(exc), validation_status="invalid_response")
        except ReviewedExtractionConflict as exc:
            self.registry.fail_job(job["job_id"], str(exc), max_attempts=1)
            outcome.update(outcome="failed", error=str(exc))
        except (_Permanent,) + _permanent_error_types() as exc:
            message = str(exc) if isinstance(exc, _Permanent) else f"{type(exc).__name__}: {exc}"
            self.registry.fail_job(job["job_id"], message, max_attempts=1)
            outcome.update(outcome="failed", error=message)
        except Exception as exc:  # untrusted model/IO failures must not stop the loop
            status = self.registry.fail_job(job["job_id"], f"{type(exc).__name__}: {exc}",
                                            self.max_attempts)
            outcome.update(outcome="retry" if status == "queued" else "failed",
                           error=f"{type(exc).__name__}: {exc}")
        return outcome

    def process_pending(self, limit: int = 100) -> list:
        results = []
        while len(results) < limit:
            result = self.process_next()
            if result is None:
                break
            results.append(result)
        return results

    def _delete(self, job) -> str:
        document = self.registry.get_document(job["document_id"])
        if document is None or document["status"] != "deleted":
            return "skipped_restored"  # the file came back before this tombstone ran
        self.graph.remove_document(job["document_id"], run_id=f"job:{job['job_id']}")
        return "removed"

    def _process(self, job) -> str:
        document = self.registry.get_document(job["document_id"])
        if (document is None or document["status"] == "deleted"
                or document["content_hash"] != job["content_hash"]):
            return "skipped_superseded"  # a newer version or a deletion owns this document
        path = Path(document["path"])
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise _Permanent(f"unsupported file type {path.suffix or '(none)'}")
        if self.registry.hash_file(path) != job["content_hash"]:
            raise RuntimeError("file changed after it was queued")  # retryable
        loaded = self.document_loader(path)
        parse = self.parser(loaded) if self.parser is not None else None
        source = {"document_id": document["document_id"],
                  "content_hash": job["content_hash"], "version": document["version"],
                  "source_file": path.name,
                  "document_pages": loaded.summary() if hasattr(loaded, "summary") else None}
        result = extract_validated(self.provider, loaded, source, parse=parse)
        if self.registry.hash_file(path) != job["content_hash"]:
            raise RuntimeError("file changed while it was being processed")  # retryable
        record = result["record"]
        record["review_status"] = REVIEW_STATUS  # never auto-approve in this phase
        self.graph.sync(document["document_id"], job["content_hash"], record,
                        filename=path.name,
                        evidence={"provenance": result["provenance"],
                                  "warnings": result["warnings"]},
                        warnings=result["warnings"],
                        allow_same_version_replace=job["action"] == "reprocess",
                        version=document["version"], run_id=f"job:{job['job_id']}")
        return "synced"


def build_parser(settings):
    """Parser callable for the configured engine; digital-PDF text needs no engine.

    The OCR engine is created lazily on first use, so queue-only runs load nothing.
    """
    from .extraction.local_ocr import build_engine, parse_document

    state = {"engine": None}

    def parser(document):
        pages = getattr(document, "pages", [])
        needs_ocr = any(getattr(page, "source", "image") != "pdf_text" for page in pages)
        if settings.parser_engine == "none":
            return parse_document(document, None) if pages and not needs_ocr else None
        if needs_ocr and state["engine"] is None:
            state["engine"] = build_engine(settings.parser_engine, settings)
        return parse_document(document, state["engine"])

    return parser


def build_provider(settings):
    """Instantiate the configured provider (lazy imports; nothing loads for 'none')."""
    if settings.provider == "none":
        return None
    if settings.provider == "donut":
        from .extraction.providers import DonutProvider
        return DonutProvider(settings.device)
    if settings.provider == "ocr":
        from .extraction.ocr_mapping import OcrLayoutProvider
        return OcrLayoutProvider()
    if settings.provider == "vision-api":
        from .extraction.vision_api import VisionApiProvider
        return VisionApiProvider(settings)
    raise ValueError(f"unsupported provider {settings.provider!r}")


class _Permanent(Exception):
    """Failure that retrying cannot fix."""
