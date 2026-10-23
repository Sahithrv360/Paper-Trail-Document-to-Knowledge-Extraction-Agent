"""Connects the registry's queued jobs to extraction and graph synchronization.

Phase contract: every extraction is stored with review_status="needs_review". Nothing
is auto-approved in this phase, whatever the arithmetic checks report; warnings are
recorded alongside the record for the reviewer. Model output is untrusted until it
passes schema validation, and the graph is only touched after validation succeeds, so
a failed job leaves the last valid graph state intact.
"""

from pathlib import Path

from .extraction.providers import InvalidModelResponse, extract_validated
from .graph import GraphStore, ReviewedExtractionConflict
from .ingestion.registry import DocumentRegistry

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
REVIEW_STATUS = "needs_review"


def load_image(path: Path):
    from PIL import Image  # optional dependency, imported lazily

    with Image.open(path) as opened:
        return opened.convert("RGB")


class JobProcessor:
    def __init__(self, registry: DocumentRegistry, graph: GraphStore, provider,
                 image_loader=load_image, max_attempts: int = 3):
        self.registry = registry
        self.graph = graph
        self.provider = provider
        self.image_loader = image_loader
        self.max_attempts = max_attempts

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
        except _Permanent as exc:
            self.registry.fail_job(job["job_id"], str(exc), max_attempts=1)
            outcome.update(outcome="failed", error=str(exc))
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
        self.graph.remove_document(job["document_id"])
        return "removed"

    def _process(self, job) -> str:
        document = self.registry.get_document(job["document_id"])
        if (document is None or document["status"] == "deleted"
                or document["content_hash"] != job["content_hash"]):
            return "skipped_superseded"  # a newer version or a deletion owns this document
        path = Path(document["path"])
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise _Permanent(f"unsupported file type {path.suffix or '(none)'}: "
                             "PDF/OCR extraction is not implemented")
        if self.registry.hash_file(path) != job["content_hash"]:
            raise RuntimeError("file changed after it was queued")  # retryable
        source = {"document_id": document["document_id"],
                  "content_hash": job["content_hash"], "version": document["version"],
                  "source_file": path.name}
        result = extract_validated(self.provider, self.image_loader(path), source)
        record = result["record"]
        record["review_status"] = REVIEW_STATUS  # never auto-approve in this phase
        self.graph.sync(document["document_id"], job["content_hash"], record,
                        filename=path.name,
                        evidence={"provenance": result["provenance"],
                                  "warnings": result["warnings"]},
                        warnings=result["warnings"],
                        allow_same_version_replace=job["action"] == "reprocess")
        return "synced"


class _Permanent(Exception):
    """Failure that retrying cannot fix."""
