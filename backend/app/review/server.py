"""Local review and graph-inspection server (standard library only).

Run::

    python -m backend.app.review.server            # prints a one-time URL with a token

Security model (single local user):

* Binds to a loopback address only; any other bind address is refused.
* Every ``/api/`` request needs the ``X-Review-Token`` header (constant-time compare).
  The token comes from ``REVIEW_TOKEN`` or is generated at start-up and printed. The
  page reads it from the URL fragment, which browsers never send to servers.
* ``Host`` must be a loopback name (DNS-rebinding protection); a cross-origin
  ``Origin`` is rejected; writes must be ``application/json`` (blocks form CSRF) and
  at most 1 MB.
* Source pages are served only for registered documents whose file still has the
  hash of the extraction being reviewed; no path from the request reaches the disk.
* Reviews go through ``GraphStore.apply_review`` with optimistic concurrency
  (``expected_extraction_id``), so a reviewer never approves a version they did not see.
"""

import argparse
import hmac
import json
import mimetypes
import secrets
import threading
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_BODY_BYTES = 1024 * 1024
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' blob:; style-src 'self'; "
                                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                                "base-uri 'none'; form-action 'none'"),
}


class ReviewError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class ReviewService:
    """The data operations behind the HTTP layer (also usable directly in tests)."""

    def __init__(self, graph, registry=None, settings=None, neo4j_driver=None,
                 neo4j_database=None, answer_provider_factory=None):
        self.graph, self.registry, self.settings = graph, registry, settings
        self.neo4j_driver, self.neo4j_database = neo4j_driver, neo4j_database
        self.answer_provider_factory = answer_provider_factory
        self._page_cache = OrderedDict()
        self._cache_lock = threading.Lock()

    # -- reads -----------------------------------------------------------------------

    def documents(self) -> list:
        synced = {row["document_id"]: dict(row) for row in self.graph.synced_documents()}
        for document_id, row in synced.items():
            if row.get("state") == "active":  # show the reviewed (effective) status
                current = self.graph.get_extraction(document_id)
                if current is not None:
                    row["validation_status"] = (current["effective_record"].get("validation_status")
                                                or row.get("validation_status"))
        rows = []
        registered = self.registry.documents() if self.registry else []
        seen = set()
        for document in registered:
            entry = synced.get(document["document_id"], {})
            rows.append({"document_id": document["document_id"],
                         "file": Path(document["path"]).name,
                         "file_status": document["status"], "file_version": document["version"],
                         "last_error": document["last_error"],
                         "extraction_state": entry.get("state"),
                         "review_status": entry.get("review_status"),
                         "validation_status": entry.get("validation_status"),
                         "synced_version": entry.get("version"),
                         "last_synced_at": entry.get("last_synced_at")})
            seen.add(document["document_id"])
        for document_id, entry in synced.items():
            if document_id not in seen:
                rows.append({"document_id": document_id, "file": entry.get("source_file"),
                             "file_status": None, "file_version": None, "last_error": None,
                             "extraction_state": entry.get("state"),
                             "review_status": entry.get("review_status"),
                             "validation_status": entry.get("validation_status"),
                             "synced_version": entry.get("version"),
                             "last_synced_at": entry.get("last_synced_at")})
        return sorted(rows, key=lambda r: (r["file"] or "", r["document_id"]))

    def document(self, document_id: str) -> dict:
        extraction = self.graph.get_extraction(document_id)
        history = self.graph.extraction_history(document_id)
        if extraction is None and not history:
            raise ReviewError(HTTPStatus.NOT_FOUND, "Unknown document")
        versions = [{key: item[key] for key in ("extraction_id", "version", "content_hash", "state",
                                                "review_status", "validation_status", "provider",
                                                "model_revision", "created_at", "ended_at")}
                    | {"reviews": item["reviews"]} for item in history]
        file_state = None
        if self.registry is not None and extraction is not None:
            registered = self.registry.get_document(document_id)
            file_state = self._file_state(registered, extraction["content_hash"])
        return {"document_id": document_id, "extraction": extraction, "versions": versions,
                "changes": self.graph.changes(document_id), "file_state": file_state}

    def _file_state(self, registered, content_hash):
        if registered is None:
            return "unregistered"
        if registered["status"] == "deleted":
            return "deleted"
        return "current" if registered["content_hash"] == content_hash else "changed"

    def page_image(self, document_id: str, page: int) -> bytes:
        if self.registry is None:
            raise ReviewError(HTTPStatus.NOT_FOUND, "No registry configured")
        extraction = self.graph.get_extraction(document_id)
        registered = self.registry.get_document(document_id)
        if extraction is None or registered is None:
            raise ReviewError(HTTPStatus.NOT_FOUND, "Unknown document")
        if self._file_state(registered, extraction["content_hash"]) != "current":
            raise ReviewError(HTTPStatus.CONFLICT,
                              "The source file no longer matches the extraction under review")
        path = Path(registered["path"])
        if self.registry.hash_file(path) != extraction["content_hash"]:
            raise ReviewError(HTTPStatus.CONFLICT, "The source file changed on disk")
        key = (document_id, extraction["content_hash"], page)
        with self._cache_lock:
            if key in self._page_cache:
                self._page_cache.move_to_end(key)
                return self._page_cache[key]
        from ..extraction.document_input import DocumentLoadError, load_document
        settings = self.settings
        try:
            loaded = (load_document(path, settings.max_pages, settings.max_page_pixels,
                                    settings.pdf_render_dpi) if settings else load_document(path))
        except DocumentLoadError as exc:
            raise ReviewError(HTTPStatus.UNPROCESSABLE_ENTITY, f"Cannot render the source: {exc}")
        if not 1 <= page <= len(loaded.pages):
            raise ReviewError(HTTPStatus.NOT_FOUND, "No such page")
        data = loaded.pages[page - 1].image_png
        with self._cache_lock:
            self._page_cache[key] = data
            while len(self._page_cache) > 8:
                self._page_cache.popitem(last=False)
        return data

    def graph_export(self, reviewed_only: bool) -> dict:
        export = self.graph.export(reviewed_only=reviewed_only)
        return {**export, "suggestions": self.graph.entity_suggestions(),
                "duplicates": self.graph.duplicate_content_groups()}

    def neo4j_graph_export(self, reviewed_only: bool) -> dict:
        if self.neo4j_driver is None:
            raise ReviewError(HTTPStatus.SERVICE_UNAVAILABLE,
                              "Neo4j is not configured; set NEO4J_URI and NEO4J_PASSWORD")
        from ..neo4j_sync import export_neo4j_graph
        return export_neo4j_graph(self.neo4j_driver, self.neo4j_database, reviewed_only)

    def sync_neo4j(self, reviewed_only: bool) -> dict:
        if self.neo4j_driver is None:
            raise ReviewError(HTTPStatus.SERVICE_UNAVAILABLE,
                              "Neo4j is not configured; set NEO4J_URI and NEO4J_PASSWORD")
        from ..neo4j_sync import sync_graph_export
        result = sync_graph_export(self.graph.export(reviewed_only=reviewed_only),
                                   self.neo4j_driver, self.neo4j_database)
        result["reviewed_only"] = reviewed_only
        return result

    def ask_graph(self, question: str, generate: bool = False,
                  include_unreviewed: bool = False, limit: int = 8) -> dict:
        if self.neo4j_driver is None:
            raise ReviewError(HTTPStatus.SERVICE_UNAVAILABLE,
                              "Neo4j is not configured; sync the graph after configuring Neo4j")
        from ..graph_rag import GraphAnswerProvider, Neo4jRetriever, summarize_graph_facts
        retrieved = Neo4jRetriever(self.neo4j_driver, self.neo4j_database).retrieve(
            question, limit=limit, reviewed_only=not include_unreviewed)
        if generate:
            settings = self.settings
            if settings is None:
                from ..config import Settings
                settings = Settings.from_env()
            try:
                factory = self.answer_provider_factory or GraphAnswerProvider
                provider = factory(settings)
                answer = provider.answer(question, retrieved)
                retrieved["context_truncated"] = (retrieved.get("context_truncated", False)
                                                   or getattr(provider, "context_truncated", False))
            except RuntimeError as exc:
                raise ReviewError(HTTPStatus.SERVICE_UNAVAILABLE, str(exc)) from exc
        else:
            answer = summarize_graph_facts(retrieved)
        return {"answer": answer, "sources": retrieved["sources"],
                "retrieval_count": len(retrieved["matches"]),
                "context_truncated": bool(retrieved.get("context_truncated")),
                "reviewed_only": retrieved["reviewed_only"]}

    # -- writes ----------------------------------------------------------------------

    def review(self, document_id: str, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ReviewError(HTTPStatus.BAD_REQUEST, "Body must be a JSON object")
        decision = payload.get("decision")
        expected = payload.get("expected_extraction_id")
        reviewer = (payload.get("reviewer") or "").strip()[:80] or None
        note = (payload.get("note") or "").strip()[:2000] or None
        if decision not in ("approved", "corrected"):
            raise ReviewError(HTTPStatus.BAD_REQUEST, "decision must be 'approved' or 'corrected'")
        current = self.graph.get_extraction(document_id)
        if current is None:
            raise ReviewError(HTTPStatus.NOT_FOUND, "No active extraction for this document")
        if expected != current["extraction_id"]:
            raise ReviewError(HTTPStatus.CONFLICT,
                              "A newer extraction exists; reload before reviewing")
        corrected = payload.get("corrected_record") if decision == "corrected" else None
        try:
            result = self.graph.apply_review(document_id, decision, corrected,
                                             reviewer=reviewer, note=note, run_id="review-ui")
        except ValueError as exc:
            raise ReviewError(HTTPStatus.UNPROCESSABLE_ENTITY, f"Correction rejected: {exc}")
        return {"review_id": result["review_id"], "change": result["change"],
                "document": self.document(document_id)}


def make_handler(service: ReviewService, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PaperTrailReview"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep request logs free of tokens and data
            pass

        # -- plumbing ----------------------------------------------------------------

        def _send(self, status, body: bytes, content_type: str, cache: str = "no-store"):
            self.send_response(status)
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, value):
            self._send(status, json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, status, message):
            self._json(status, {"error": message})

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip()
            name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
            return name.lower() in LOOPBACK_HOSTS

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            return urlsplit(origin).netloc == self.headers.get("Host")

        def _authorized(self) -> bool:
            supplied = self.headers.get("X-Review-Token") or ""
            return hmac.compare_digest(supplied.encode(), token.encode())

        def _guard(self, api: bool) -> bool:
            if not self._host_ok():
                self._error(HTTPStatus.MISDIRECTED_REQUEST, "Host must be a loopback address")
                return False
            if not self._origin_ok():
                self._error(HTTPStatus.FORBIDDEN, "Cross-origin request refused")
                return False
            if api and not self._authorized():
                self._error(HTTPStatus.UNAUTHORIZED, "Missing or wrong review token")
                return False
            return True

        # -- routes --------------------------------------------------------------------

        def do_GET(self):
            url = urlsplit(self.path)
            route = url.path
            if route in STATIC_FILES:
                if not self._guard(api=False):
                    return
                name = STATIC_FILES[route]
                body = (STATIC_DIR / name).read_bytes()
                kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
                if kind.startswith("text/") or kind.endswith("javascript"):
                    kind += "; charset=utf-8"
                return self._send(HTTPStatus.OK, body, kind)
            if not route.startswith("/api/"):
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            if not self._guard(api=True):
                return
            parts = [part for part in route.split("/") if part][1:]
            query = parse_qs(url.query)
            try:
                if parts == ["documents"]:
                    return self._json(HTTPStatus.OK, service.documents())
                if len(parts) == 2 and parts[0] == "documents":
                    return self._json(HTTPStatus.OK, service.document(parts[1]))
                if (len(parts) == 4 and parts[0] == "documents" and parts[2] == "pages"
                        and parts[3].endswith(".png") and parts[3][:-4].isdigit()):
                    data = service.page_image(parts[1], int(parts[3][:-4]))
                    return self._send(HTTPStatus.OK, data, "image/png", cache="private, max-age=60")
                if parts == ["graph"]:
                    reviewed = query.get("reviewed_only", ["0"])[0] in ("1", "true")
                    return self._json(HTTPStatus.OK, service.graph_export(reviewed))
                if parts == ["neo4j", "graph"]:
                    reviewed = query.get("reviewed_only", ["0"])[0] in ("1", "true")
                    return self._json(HTTPStatus.OK, service.neo4j_graph_export(reviewed))
                if parts == ["changes"]:
                    return self._json(HTTPStatus.OK, service.graph.changes())
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            except ReviewError as exc:
                return self._error(exc.status, exc.message)
            except Exception as exc:  # pragma: no cover - surfaced to the reviewer
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}")

        def do_POST(self):
            route = urlsplit(self.path).path
            if not route.startswith("/api/"):
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            if not self._guard(api=True):
                return
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send application/json")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._error(HTTPStatus.BAD_REQUEST, "Bad Content-Length")
            if length > MAX_BODY_BYTES:
                # Discard (without storing) a moderately oversized body so the client
                # receives the 413 instead of a reset connection; give up past 8 MB.
                remaining = length if length <= 8 * MAX_BODY_BYTES else 0
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self.close_connection = True
                return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Body exceeds 1 MB")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "null")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._error(HTTPStatus.BAD_REQUEST, "Body is not valid JSON")
            if not isinstance(payload, dict):
                return self._error(HTTPStatus.BAD_REQUEST, "Body must be a JSON object")
            parts = [part for part in route.split("/") if part][1:]
            try:
                if len(parts) == 3 and parts[0] == "documents" and parts[2] == "review":
                    return self._json(HTTPStatus.OK, service.review(parts[1], payload))
                if parts == ["neo4j", "sync"]:
                    reviewed_only = payload.get("reviewed_only", False)
                    if not isinstance(reviewed_only, bool):
                        return self._error(HTTPStatus.BAD_REQUEST, "reviewed_only must be boolean")
                    return self._json(HTTPStatus.OK, service.sync_neo4j(
                        reviewed_only))
                if parts == ["graph-rag"]:
                    question = payload.get("question")
                    if not isinstance(question, str) or not question.strip():
                        return self._error(HTTPStatus.BAD_REQUEST, "question must be non-empty text")
                    generate = payload.get("generate", False)
                    include_unreviewed = payload.get("include_unreviewed", False)
                    limit = payload.get("limit", 8)
                    if not isinstance(generate, bool) or not isinstance(include_unreviewed, bool):
                        return self._error(HTTPStatus.BAD_REQUEST,
                                           "generate and include_unreviewed must be boolean")
                    if not isinstance(limit, int) or isinstance(limit, bool):
                        return self._error(HTTPStatus.BAD_REQUEST, "limit must be an integer")
                    return self._json(HTTPStatus.OK, service.ask_graph(
                        question, generate=generate,
                        include_unreviewed=include_unreviewed, limit=limit))
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            except ReviewError as exc:
                return self._error(exc.status, exc.message)
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # do not return provider or database details to the browser
                return self._error(HTTPStatus.BAD_GATEWAY, f"{type(exc).__name__}")

        def do_HEAD(self):
            self.do_GET()

    return Handler


def create_server(service: ReviewService, host: str = "127.0.0.1", port: int = 8765,
                  token: str = None):
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("The review server only binds to a loopback address")
    token = token or secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((host, port), make_handler(service, token))
    server.daemon_threads = True
    server.review_token = token
    return server


def main(argv=None):
    import os
    from ..config import DATABASE_PATH, GRAPH_PATH, Settings
    from ..graph import GraphStore
    from ..ingestion.registry import DocumentRegistry

    parser = argparse.ArgumentParser(description="Local Paper Trail review server")
    parser.add_argument("--port", type=int, default=int(os.getenv("REVIEW_PORT", "8765")))
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "::1", "localhost"])
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    neo4j_driver = None
    if os.getenv("NEO4J_URI") and os.getenv("NEO4J_PASSWORD"):
        from ..neo4j_sync import create_driver
        neo4j_driver = create_driver()
    service = ReviewService(GraphStore(GRAPH_PATH), DocumentRegistry(DATABASE_PATH), settings,
                            neo4j_driver=neo4j_driver,
                            neo4j_database=os.getenv("NEO4J_DATABASE") or None)
    server = create_server(service, args.host, args.port, os.getenv("REVIEW_TOKEN") or None)
    host = f"[{args.host}]" if ":" in args.host else args.host
    print(f"Paper Trail review: http://{host}:{server.server_address[1]}/#token={server.review_token}")
    print("Keep this URL private; anyone with it can approve extractions on this machine.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if neo4j_driver is not None:
            neo4j_driver.close()


if __name__ == "__main__":
    main()
