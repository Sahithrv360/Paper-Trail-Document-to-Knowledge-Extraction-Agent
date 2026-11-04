"""Local review and graph-inspection server (standard library only).

Run::

    python -m backend.app.review.server            # prints a one-time URL with a token

Security model (single local user):

* Binds to a loopback address only; any other bind address is refused.
* Every ``/api/`` request needs the ``X-Review-Token`` header (constant-time compare).
  The token comes from ``REVIEW_TOKEN`` or is generated at start-up and printed. The
  page reads it from the URL fragment, which browsers never send to servers.
* ``Host`` must be a loopback name (DNS-rebinding protection); a cross-origin
  ``Origin`` is rejected; JSON writes are limited to 1 MB. A dedicated authenticated
  multipart route accepts one supported file up to the configured file-size limit.
* Source pages are served only for registered documents whose file still has the
  hash of the extraction being reviewed; no path from the request reaches the disk.
* Reviews go through ``GraphStore.apply_review`` with optimistic concurrency
  (``expected_extraction_id``), so a reviewer never approves a version they did not see.
"""

import argparse
from email.parser import BytesParser
from email.policy import default as email_policy
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css"}
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
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


def build_frontend_if_needed() -> bool:
    """Keep the backend's served SPA current without rebuilding on every start."""
    entry = FRONTEND_DIST_DIR / "index.html"
    inputs = [FRONTEND_DIR / "index.html", FRONTEND_DIR / "vite.config.js",
              FRONTEND_DIR / "package.json", FRONTEND_DIR / "package-lock.json"]
    for directory in (FRONTEND_DIR / "src", FRONTEND_DIR / "public"):
        if directory.is_dir():
            inputs.extend(path for path in directory.rglob("*") if path.is_file())
    newest_input = max((path.stat().st_mtime for path in inputs if path.is_file()), default=0)
    if entry.is_file() and entry.stat().st_mtime >= newest_input:
        return True

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        print("Frontend build skipped: npm was not found; the bundled legacy review page will be used.")
        return False
    print("Building the updated web UI...")
    result = subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR,
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print("Frontend build failed; the bundled legacy review page will be used.")
        if result.stdout:
            print(result.stdout[-2000:])
        if result.stderr:
            print(result.stderr[-2000:])
        return False
    print("Web UI build is ready.")
    return True


class ReviewError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class ReviewService:
    """The data operations behind the HTTP layer (also usable directly in tests)."""

    def __init__(self, graph, registry=None, settings=None, neo4j_driver=None,
                 neo4j_database=None, answer_provider_factory=None):
        self.graph, self.registry, self.settings = graph, registry, settings
        self.completion_threshold = getattr(settings, "document_completion_threshold", 0.80)
        self.neo4j_driver, self.neo4j_database = neo4j_driver, neo4j_database
        self.answer_provider_factory = answer_provider_factory
        self._page_cache = OrderedDict()
        self._cache_lock = threading.Lock()
        try:
            self._graph_llm_limit = max(0, int(os.getenv("GRAPH_RAG_MAX_CALLS_PER_HOUR", "10")))
        except ValueError:
            self._graph_llm_limit = 10
        self._graph_llm_calls = deque()
        self._graph_answer_cache = OrderedDict()
        self._graph_llm_lock = threading.Lock()
        self._agent_runs = OrderedDict()
        self._agent_runs_lock = threading.Lock()

    def _update_agent_run(self, request_id: str, event: dict) -> None:
        with self._agent_runs_lock:
            state = self._agent_runs.setdefault(request_id, {
                "request_id": request_id, "status": "running", "stage": "accepted", "events": []})
            state.update({key: value for key, value in event.items()
                          if key in {"status", "stage", "error"}})
            if event.get("event"):
                state["events"].append({"stage": event.get("stage"), "event": event["event"]})
                state["events"] = state["events"][-32:]
            state["updated_at"] = time.time()
            self._agent_runs.move_to_end(request_id)
            while len(self._agent_runs) > 100:
                self._agent_runs.popitem(last=False)

    def agent_run(self, request_id: str):
        with self._agent_runs_lock:
            state = self._agent_runs.get(request_id)
            return dict(state) if state else None

    # -- reads -----------------------------------------------------------------------

    def documents(self) -> list:
        from ..extraction.confidence import estimate_extraction_confidence

        synced = {row["document_id"]: dict(row)
                  for row in self.graph.synced_documents(include_records=True)}
        for document_id, row in synced.items():
            if row.get("state") == "active":  # show the reviewed (effective) status
                raw_record = row.get("corrected_record") or row.get("active_record")
                if raw_record:
                    record = json.loads(raw_record)
                    row["validation_status"] = (record.get("validation_status")
                                                or row.get("validation_status"))
                    row["confidence_score"] = record.get("confidence_score")
                    if row["confidence_score"] is None:
                        row["confidence_score"] = estimate_extraction_confidence(record)
                    row["confidence_score"] = max(0.0, min(1.0, float(row["confidence_score"])))
                    row["confidence_basis"] = "rule_based_quality_proxy"
                    row["completion_threshold"] = self.completion_threshold
                    row["completion_status"] = (
                        "complete" if row["confidence_score"] > self.completion_threshold
                        and row["validation_status"] == "valid_reconciled" else "needs_review")
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
                         "confidence_score": entry.get("confidence_score"),
                         "confidence_basis": entry.get("confidence_basis"),
                         "completion_threshold": entry.get("completion_threshold", self.completion_threshold),
                         "completion_status": entry.get("completion_status"),
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
                             "confidence_score": entry.get("confidence_score"),
                             "confidence_basis": entry.get("confidence_basis"),
                             "completion_threshold": entry.get("completion_threshold", self.completion_threshold),
                             "completion_status": entry.get("completion_status"),
                             "synced_version": entry.get("version"),
                             "last_synced_at": entry.get("last_synced_at")})
        return sorted(rows, key=lambda r: (r["file"] or "", r["document_id"]))

    def document(self, document_id: str) -> dict:
        from ..extraction.confidence import estimate_extraction_confidence

        extraction = self.graph.get_extraction(document_id)
        history = self.graph.extraction_history(document_id)
        if extraction is None and not history:
            raise ReviewError(HTTPStatus.NOT_FOUND, "Unknown document")
        versions = [{key: item[key] for key in ("extraction_id", "version", "content_hash", "state",
                                                "review_status", "validation_status", "provider",
                                                "model_revision", "created_at", "ended_at")}
                    | {"reviews": item["reviews"]} for item in history]
        file_state = None
        if extraction is not None:
            for key in ("effective_record", "record"):
                record = extraction.get(key)
                if isinstance(record, dict):
                    if record.get("confidence_score") is None:
                        record["confidence_score"] = estimate_extraction_confidence(record)
                    record["confidence_score"] = max(0.0, min(1.0, float(record["confidence_score"])))
                    extraction["confidence_score"] = record["confidence_score"]
                    extraction["confidence_basis"] = "rule_based_quality_proxy"
                    extraction["completion_threshold"] = self.completion_threshold
                    extraction["completion_status"] = (
                        "complete" if record["confidence_score"] > self.completion_threshold
                        and record.get("validation_status") == "valid_reconciled" else "needs_review")
                    break
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
        # Keep the graph view useful if Neo4j is offline or its projection is stale.
        # These deterministic links are a read-only presentation projection; SQLite
        # remains the source of truth and the Neo4j sync button can rebuild its copy.
        from ..neo4j_sync import derive_direct_invoice_relationships, derive_shared_item_hubs
        suggestion_nodes = export["nodes"]
        hub_nodes, hub_edges = derive_shared_item_hubs(export)
        direct_edges = derive_direct_invoice_relationships(export, hub_nodes, hub_edges)
        export = {**export, "nodes": [*export["nodes"], *hub_nodes],
                  "edges": [*export["edges"], *hub_edges, *direct_edges]}
        return {**export, "suggestions": self.graph.entity_suggestions(suggestion_nodes),
                "duplicates": self.graph.duplicate_content_groups()}

    def analytics(self) -> dict:
        """Return page-level counts without sending the full graph to the browser."""
        graph = self.graph.export(reviewed_only=False)
        documents = self.documents()
        from ..neo4j_sync import derive_direct_invoice_relationships, derive_shared_item_hubs
        hub_nodes, hub_edges = derive_shared_item_hubs(graph)
        direct_edges = derive_direct_invoice_relationships(graph, hub_nodes, hub_edges)
        entities = [node for node in [*graph["nodes"], *hub_nodes]
                    if node["type"] != "Document"]
        invoices = [node for node in graph["nodes"] if node["type"] == "Invoice"]
        type_counts = {}
        for node in entities:
            node_type = node["type"] or "Other"
            type_counts[node_type] = type_counts.get(node_type, 0) + 1
        fact_fields = ("invoice_number", "invoice_date", "total_amount", "subtotal",
                       "tax_amount", "currency")
        fact_count = sum(sum((node.get("props") or {}).get(field) is not None
                             for field in fact_fields) for node in invoices)
        scores = [float(row["confidence_score"]) for row in documents
                  if row.get("confidence_score") is not None]
        pending = sum(row.get("review_status") == "needs_review"
                      and row.get("extraction_state") == "active" for row in documents)
        resolved = sum(row.get("review_status") in {"approved", "corrected"}
                       and row.get("extraction_state") == "active" for row in documents)
        colors = {"Organization": "#3b82f6", "Product": "#10b981", "Contract": "#f59e0b",
                  "Person": "#8b5cf6", "Money": "#14b8a6", "Invoice": "#06b6d4",
                  "Date": "#64748b", "Location": "#ec4899"}
        return {
            "kpis": {"documents_processed": len(documents),
                     "documents_processed_today": None,
                     "entities_extracted": len(entities), "entities_today": None,
                     "relationships_discovered": len(graph["edges"] + hub_edges + direct_edges),
                     "relationships_today": None,
                     "facts_extracted": fact_count, "facts_today": None},
            "extraction_activity_timeline": [],
            "entities_by_type": [{"name": name, "count": type_counts[name],
                                  "color": colors.get(name, "#64748b")}
                                 for name in sorted(type_counts)],
            "confidence_distribution": ([
                {"range": "95-100%", "count": sum(score >= .95 for score in scores)},
                {"range": "90-94%", "count": sum(.90 <= score < .95 for score in scores)},
                {"range": "80-89%", "count": sum(.80 <= score < .90 for score in scores)},
                {"range": "< 80%", "count": sum(score < .80 for score in scores)},
            ] if scores else []),
            "human_review_metrics": {"pending_reviews": pending,
                                     "resolved_reviews": resolved,
                                     "rejected_hallucinations": None},
        }

    def evidence(self) -> list:
        """Expose field quotations from each active extraction version."""
        result = []
        for row in self.graph.synced_documents(include_records=True):
            if row.get("state") != "active":
                continue
            raw = row.get("corrected_record") or row.get("active_record")
            if not raw:
                continue
            record = json.loads(raw) if isinstance(raw, str) else raw
            fields = record.get("field_evidence") or record.get("evidence") or {}
            source_file = (row.get("source_file")
                           or (record.get("provenance") or {}).get("source_file")
                           or row["document_id"])
            for field, item in fields.items():
                if not isinstance(item, dict):
                    continue
                quote = item.get("quote") or item.get("raw_text")
                if not quote:
                    continue
                result.append({"id": f"{row['document_id']}:{field}",
                    "source_doc_id": row["document_id"],
                    "source_doc_name": Path(source_file).name,
                    "entity_name": field, "entity_type": record.get("doc_type") or "Document",
                    "field_name": field, "source_page": item.get("page"),
                    "status": "Supported" if row.get("review_status") in {"approved", "corrected"} else "In Review",
                    "evidence_text": quote})
        return sorted(result, key=lambda item: (item["source_doc_name"], item["field_name"]))

    def runtime_status(self) -> dict:
        from ..config import MAX_FILE_BYTES, SUPPORTED_SUFFIXES, WATCH_FOLDER
        documents = self.documents()
        llm_backend = os.getenv("GRAPH_RAG_BACKEND", "anthropic").strip().lower()
        if llm_backend not in {"anthropic", "ollama"}:
            llm_backend = "hosted"
        llm_model = (os.getenv("GRAPH_RAG_MODEL") or
                     (os.getenv("OLLAMA_MODEL", "qwen3:4b") if llm_backend == "ollama"
                      else os.getenv("VISION_API_MODEL", "")))
        return {
            "watch_folder": str(WATCH_FOLDER), "watch_folder_exists": WATCH_FOLDER.is_dir(),
            "supported_extensions": sorted(SUPPORTED_SUFFIXES),
            "max_file_mb": MAX_FILE_BYTES // (1024 * 1024),
            "watcher_process": ("enabled in review server process"
                if os.getenv("PAPERTRAIL_ENABLE_WATCHER", "0").lower() in {"1", "true", "yes"}
                else "separate process; availability is not monitored"),
            "registered_documents": len(documents),
            "queued_documents": sum(row.get("file_status") in {"queued", "pending"} for row in documents),
            "failed_documents": sum(row.get("file_status") == "failed" for row in documents),
            "extraction_provider": getattr(self.settings, "provider", "none"),
            "ocr_engine": getattr(self.settings, "parser_engine", "none"),
            "device": getattr(self.settings, "device", "cpu"),
            "completion_threshold": self.completion_threshold,
            "neo4j_configured": self.neo4j_driver is not None,
            "answer_backend": llm_backend,
            "answer_model": llm_model or "not configured",
        }

    def semantic_model(self) -> list:
        from ..graph_ontology import EDGE_TYPES, NODE_TYPES
        graph = self.graph.export(reviewed_only=False)
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        result = []
        descriptions = {
            "Document": "A source file registered for extraction.",
            "Invoice": "Invoice facts extracted from a source document.",
            "Vendor": "A vendor supported by one or more source documents.",
            "Customer": "A customer supported by one or more source documents.",
            "LineItem": "An invoice line item and its extracted fields.",
            "ItemDescription": "An exact item description shared by line items.",
            "ProductCategory": "A category label, when classified.",
            "SyntheticEmail": "Synthetic email content, explicitly marked as demo data.",
            "ConversationSummary": "A synthetic summary, explicitly marked as demo data.",
        }
        for node_type in sorted(NODE_TYPES):
            typed = [node for node in nodes if node.get("type") == node_type]
            props = sorted({key for node in typed for key in (node.get("props") or {})})
            rels = [{"relation": relation, "target": target}
                    for relation, (source, target) in sorted(EDGE_TYPES.items())
                    if source == node_type]
            result.append({"id": node_type, "name": node_type,
                           "description": descriptions.get(node_type, "Ontology node type."),
                           "properties": props, "allowed_relationships": rels,
                           "instances_count": len(typed), "examples": [
                               str((node.get("props") or {}).get("invoice_number")
                                   or (node.get("props") or {}).get("name")
                                   or (node.get("props") or {}).get("title")
                                   or node.get("id")) for node in typed[:5]],
                           "color": "#2563eb"})
        return result

    def search(self, query: str) -> dict:
        term = query.strip().casefold()
        if len(term) < 2:
            return {"documents": [], "entities": [], "relationships": [], "facts": []}
        graph = self.graph.export(reviewed_only=False)
        docs = self.documents()
        def matches(value):
            return term in str(value or "").casefold()
        documents = [{"id": row["document_id"], "title": row.get("file") or row["document_id"],
                      "subtitle": row.get("review_status") or row.get("file_status") or "Registered"}
                     for row in docs if matches(row.get("file")) or matches(row.get("document_id"))][:8]
        entities, facts = [], []
        for node in graph.get("nodes", []):
            props = node.get("props") or {}
            label = (props.get("invoice_number") or props.get("name") or props.get("title")
                     or props.get("description") or node.get("id"))
            if node.get("type") != "Document" and (matches(label) or any(matches(v) for v in props.values() if isinstance(v, (str, int, float)))):
                entities.append({"id": node["id"], "title": str(label), "type": node["type"],
                                 "subtitle": node["type"]})
            if node.get("type") in {"Invoice", "LineItem"}:
                for key, value in props.items():
                    if value is not None and matches(value):
                        facts.append({"id": f"{node['id']}:{key}", "title": str(value),
                                      "subtitle": f"{key.replace('_', ' ')} · {label}"})
        by_id = {node["id"]: node for node in graph.get("nodes", [])}
        relationships = []
        for edge in graph.get("edges", []):
            source, target = by_id.get(edge["source"], {}), by_id.get(edge["target"], {})
            source_label = (source.get("props") or {}).get("invoice_number") or (source.get("props") or {}).get("name") or source.get("id")
            target_label = (target.get("props") or {}).get("invoice_number") or (target.get("props") or {}).get("name") or target.get("id")
            if matches(edge.get("type")) or matches(source_label) or matches(target_label):
                relationships.append({"id": edge["id"], "title": edge["type"],
                                      "subtitle": f"{source_label} → {target_label}"})
        return {"documents": documents[:8], "entities": entities[:8],
                "relationships": relationships[:8], "facts": facts[:8]}

    def potential_conflicts(self) -> list:
        """Only flag duplicate invoice identifiers with differing recorded values."""
        invoices = [node for node in self.graph.export().get("nodes", [])
                    if node.get("type") == "Invoice"]
        groups = {}
        for invoice in invoices:
            props = invoice.get("props") or {}
            number = str(props.get("invoice_number") or "").strip()
            if number:
                entry = groups.setdefault(number.casefold(), {"invoice_number": number, "invoices": []})
                entry["invoices"].append(invoice)
        result = []
        for entry in groups.values():
            group = entry["invoices"]
            if len(group) < 2:
                continue
            differing = [key for key in ("total_amount", "currency", "invoice_date")
                         if len({str((item.get("props") or {}).get(key)) for item in group}) > 1]
            if differing:
                result.append({"invoice_number": entry["invoice_number"],
                               "properties": differing,
                               "documents": sorted({support.get("document_id")
                                   for item in group for support in item.get("support", [])})})
        return result

    def conversation_threads(self) -> list:
        """Return only the email thread fields used by dashboard/repository cards."""
        graph = self.graph.export(reviewed_only=False)
        threads = {}
        invoices = {}
        for node in graph.get("nodes", []):
            kind = node.get("type")
            props = node.get("props") or {}
            number = str(props.get("invoice_number") or "").strip()
            if kind == "Invoice" and number:
                invoices[number] = {"id": node.get("id"), "type": kind,
                                    "label": number, "data": props}
            elif kind == "SyntheticEmail":
                number = number or "Unlinked invoice"
                thread = threads.setdefault(number, {"invoiceNumber": number,
                    "emails": [], "summary": None, "invoice": None})
                thread["emails"].append({"id": node.get("id"), "type": kind,
                                         "label": props.get("subject") or "Email",
                                         "data": props})
            elif kind == "ConversationSummary" and number:
                thread = threads.setdefault(number, {"invoiceNumber": number,
                    "emails": [], "summary": None, "invoice": None})
                thread["summary"] = {"id": node.get("id"), "type": kind,
                                     "label": props.get("title") or "Conversation summary",
                                     "data": props}
        for number, thread in threads.items():
            thread["invoice"] = invoices.get(number)
            thread["emails"].sort(key=lambda email: int(
                (email.get("data") or {}).get("thread_position") or 0))
            props = ((thread.get("summary") or {}).get("data")
                     or (thread.get("invoice") or {}).get("data")
                     or ((thread["emails"][0].get("data") or {}) if thread["emails"] else {}))
            thread["vendor"] = props.get("vendor_name") or props.get("vendor") or "Vendor not recorded"
            thread["customer"] = props.get("customer_name") or props.get("customer") or "Customer not recorded"
        return sorted(threads.values(), key=lambda thread: thread["invoiceNumber"])

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
                  include_unreviewed: bool = False, limit: int = 8,
                  mode: str = "hybrid", history=None, request_id: str = None) -> dict:
        if mode == "agent":
            from ..agentic_rag import PaperTrailAgentStore, run_papertrail_agent
            from kg_agent.runtime.tools.graph import party_product_search

            request_id = request_id or secrets.token_hex(16)
            self._update_agent_run(request_id, {"status": "running", "stage": "accepted"})
            graph_export = self.graph_export(reviewed_only=not include_unreviewed)
            used_unreviewed_product_fallback = False
            if not include_unreviewed:
                all_export = self.graph.export(reviewed_only=False)
                reviewed_product_matches = party_product_search(
                    PaperTrailAgentStore(graph_export), question, limit=1)
                all_product_matches = party_product_search(
                    PaperTrailAgentStore(all_export), question, limit=1)
                if not reviewed_product_matches and all_product_matches:
                    # Product lookups should expose existing invoice facts even when
                    # every matching machine extraction is awaiting review. The answer
                    # carries needs_review on every citation so this is never presented
                    # as reviewed evidence.
                    graph_export = all_export
                    used_unreviewed_product_fallback = True
                synthetic_edge_rows = [edge for edge in all_export.get("edges", [])
                    if (edge.get("props") or {}).get("synthetic") is True]
                synthetic_linked_ids = {node_id for edge in synthetic_edge_rows
                                        for node_id in (edge["source"], edge["target"])}
                synthetic_customer_names = {str((node.get("props") or {}).get("customer_name", "")).casefold()
                    for node in all_export.get("nodes", [])
                    if node.get("type") == "ConversationSummary"
                    and (node.get("props") or {}).get("synthetic") is True}
                synthetic_customer_nodes = [node for node in all_export.get("nodes", [])
                    if node.get("type") == "Customer"
                    and str((node.get("props") or {}).get("name", "")).casefold()
                        in synthetic_customer_names]
                synthetic_linked_ids.update(node["id"] for node in synthetic_customer_nodes)
                exact_hidden = []
                for node in all_export.get("nodes", []):
                    props = node.get("props") or {}
                    mentions = [props.get("name"), props.get("invoice_number"),
                                props.get("filename")]
                    if str(props.get("name", "")).casefold() in synthetic_customer_names:
                        continue
                    if any(value and re.search(r"(?<![\w])" + re.escape(str(value)) +
                            r"(?![\w])", question, flags=re.IGNORECASE)
                           for value in mentions):
                        if (node["id"] not in {item["id"] for item in graph_export["nodes"]}
                                and node["id"] not in synthetic_linked_ids):
                            exact_hidden.append(node)
                if exact_hidden:
                    match = exact_hidden[0]
                    props = match.get("props") or {}
                    display_name = props.get("name") or props.get("invoice_number") or match["id"]
                    answer = (f"I found {display_name} in the graph, but its source is not "
                        "reviewed yet. Agent mode uses reviewed evidence by default, so I "
                        "excluded it rather than mixing it with approved records. Review the "
                        "invoice or enable unreviewed records to query it.")
                    self._update_agent_run(request_id, {"status": "completed", "stage": "completed",
                        "event": {"stage": "review_filter", "event": {"step": "review_filter",
                            "status": "excluded_unreviewed", "entity": display_name}}})
                    return {"answer": answer, "sources": [], "links": [], "retrieval_count": 0,
                        "reviewed_only": True, "answer_mode": "local_review_filter",
                        "retrieval_backend": "sqlite+kg_agent", "confidence": 0.0,
                        "pipeline_steps": [{"step": 1, "name": "Review Filter",
                            "description": "Exact entity match found, but its source has not been reviewed.",
                            "detail": f"{display_name} was excluded from this answer."}],
                        "request_id": request_id}
                # Synthetic emails are explicitly fictional demo evidence. Keep their
                # declared graph neighborhood available, matching the existing RAG path.
                full_nodes = {node["id"]: node for node in all_export.get("nodes", [])}
                demo_nodes = [node for node in all_export.get("nodes", [])
                              if (node.get("props") or {}).get("synthetic") is True]
                demo_edges = synthetic_edge_rows
                demo_node_ids = {node["id"] for node in demo_nodes}
                demo_node_ids.update(node["id"] for node in synthetic_customer_nodes)
                for edge in demo_edges:
                    demo_node_ids.update((edge["source"], edge["target"]))
                merged_nodes = {node["id"]: node for node in graph_export.get("nodes", [])}
                for node_id in demo_node_ids:
                    if node_id in full_nodes:
                        merged_nodes.setdefault(node_id, full_nodes[node_id])
                merged_edges = {edge["id"]: edge for edge in graph_export.get("edges", [])}
                for edge in demo_edges:
                    merged_edges.setdefault(edge["id"], edge)
                graph_export = {**graph_export, "nodes": list(merged_nodes.values()),
                                "edges": list(merged_edges.values())}
            # Do not let general keyword matches masquerade as an answer to an
            # explicit customer email-history question. Use the graph's invoice
            # neighborhood to report invoice evidence and state when no thread exists.
            question_lower = question.casefold()
            asks_about_email = any(term in question_lower for term in
                                   ("email", "conversation", "correspondence", "thread",
                                    "communication", "message"))
            if asks_about_email:
                nodes_by_id = {node["id"]: node for node in graph_export.get("nodes", [])}
                matched_customers = [node for node in graph_export.get("nodes", [])
                    if node.get("type") == "Customer"
                    and (node.get("props") or {}).get("name")
                    and re.search(r"(?<![\w])" + re.escape(str(node["props"]["name"])) +
                                  r"(?![\w])", question, flags=re.IGNORECASE)]
                for customer in matched_customers:
                    invoice_ids = {edge["target"] for edge in graph_export.get("edges", [])
                        if edge.get("source") == customer["id"] and edge.get("type") == "BILLED_TO"}
                    invoice_ids.update(edge["source"] for edge in graph_export.get("edges", [])
                        if edge.get("target") == customer["id"] and edge.get("type") == "BILLED_TO")
                    invoice_numbers = {str((nodes_by_id.get(invoice_id, {}).get("props") or {}).get("invoice_number"))
                                       for invoice_id in invoice_ids if nodes_by_id.get(invoice_id)}
                    related_email_nodes = [node for node in graph_export.get("nodes", [])
                        if node.get("type") in {"SyntheticEmail", "ConversationSummary"}
                        and str((node.get("props") or {}).get("invoice_number", "")) in invoice_numbers]
                    if related_email_nodes:
                        continue
                    line_items = []
                    for edge in graph_export.get("edges", []):
                        if edge.get("type") == "CONTAINS" and edge.get("source") in invoice_ids:
                            item = nodes_by_id.get(edge.get("target"))
                            if item and item.get("type") == "LineItem":
                                line_items.append(item)
                    invoices = [nodes_by_id[item_id] for item_id in sorted(invoice_ids)
                                if item_id in nodes_by_id]
                    customer_name = customer["props"]["name"]
                    if invoices:
                        invoice = invoices[0]
                        props = invoice.get("props") or {}
                        descriptions = []
                        for item in line_items:
                            item_props = item.get("props") or {}
                            quantity = item_props.get("quantity")
                            description = item_props.get("description") or "item not described"
                            descriptions.append(f"{quantity:g} × {description}" if isinstance(quantity, (int, float)) else description)
                        bought = "\n- ".join(descriptions) or "no line items are linked"
                        total = props.get("total_amount")
                        invoice_number = props.get("invoice_number", "unknown")
                        review_status = str(props.get("review_status", "unknown")).replace("_", " ")
                        answer = f"I found an {review_status} invoice, {invoice_number}, for {customer_name}."
                        if isinstance(total, (int, float)):
                            answer += f" It totals {props.get('currency', '')}{total:.2f}."
                        if line_items:
                            answer += " It includes:\n- " + bought
                        else:
                            answer += " I couldn't find any linked line-item descriptions."
                        answer += ("\nI couldn't find an email or conversation linked to this invoice "
                                   "in the available records.")
                    else:
                        answer = (f"I found {customer_name}, but no linked invoice or email conversation "
                                  "in the available graph.")
                    source_ids = [customer["id"], *[item["id"] for item in invoices],
                                  *[item["id"] for item in line_items]]
                    sources = [{"id": nodes_by_id[item_id]["id"], "type": nodes_by_id[item_id]["type"],
                                "invoice_number": (nodes_by_id[item_id].get("props") or {}).get("invoice_number"),
                                "filename": (nodes_by_id[item_id].get("props") or {}).get("filename"),
                                "data": nodes_by_id[item_id].get("props") or {}}
                               for item_id in source_ids if item_id in nodes_by_id]
                    self._update_agent_run(request_id, {"status": "completed", "stage": "completed",
                        "event": {"stage": "evidence_check", "event": {"step": "evidence_check",
                            "status": "no_related_conversation", "customer": customer_name}}})
                    return {"answer": answer, "sources": sources, "links": [], "retrieval_count": len(sources),
                        "reviewed_only": False, "answer_mode": "agent_graph_evidence_check",
                        "retrieval_backend": "sqlite+kg_agent", "confidence": None,
                        "citations": [source["id"] for source in sources],
                        "pipeline_steps": [
                            {"step": 1, "name": "Entity Resolution", "description": "Matched the named customer in the graph.", "detail": customer_name},
                            {"step": 2, "name": "Invoice Retrieval", "description": "Followed customer to invoice and line item records.", "detail": f"{len(invoices)} invoices; {len(line_items)} line items."},
                            {"step": 3, "name": "Conversation Check", "description": "Checked linked email and conversation summary nodes.", "detail": "No email conversation is linked to this customer's invoice."},
                            {"step": 4, "name": "Evidence Grounding", "description": "Returned the invoice facts and stated the missing conversation evidence.", "detail": f"{len(sources)} cited graph records."}],
                        "request_id": request_id}
            asks_product_frequency = (any(term in question_lower for term in
                ("product", "products", "service", "services", "item", "items",
                 "line item", "line-item"))
                and any(term in question_lower for term in
                ("most often", "most common", "common", "appear", "frequent", "across",
                 "shared", "between", "match", "related", "relationship", "count",
                 "how many")))
            if asks_product_frequency:
                nodes = {node["id"]: node for node in graph_export.get("nodes", [])}
                invoices_for_item = {}
                customers_for_invoice = {}
                item_edges_for_invoice = {}
                for edge in graph_export.get("edges", []):
                    if edge.get("type") == "CONTAINS":
                        invoice_id, item_id = edge.get("source"), edge.get("target")
                        if nodes.get(invoice_id, {}).get("type") == "LineItem":
                            invoice_id, item_id = item_id, invoice_id
                        if nodes.get(invoice_id, {}).get("type") == "Invoice":
                            invoices_for_item.setdefault(item_id, set()).add(invoice_id)
                            item_edges_for_invoice.setdefault(item_id, set()).add(
                                (invoice_id, edge.get("id")))
                    elif edge.get("type") == "BILLED_TO":
                        invoice_id, customer_id = edge.get("source"), edge.get("target")
                        if nodes.get(invoice_id, {}).get("type") == "Customer":
                            invoice_id, customer_id = customer_id, invoice_id
                        if nodes.get(invoice_id, {}).get("type") == "Invoice":
                            customers_for_invoice.setdefault(invoice_id, set()).add(customer_id)
                requested_customers = [node for node in nodes.values()
                    if node.get("type") == "Customer" and (node.get("props") or {}).get("name")
                    and re.search(r"(?<![\w])" + re.escape(str(node["props"]["name"])) +
                                  r"(?![\w])", question, flags=re.IGNORECASE)]
                requested_customer_ids = {node["id"] for node in requested_customers}
                rows = []
                for item_id, invoice_ids in invoices_for_item.items():
                    item = nodes.get(item_id)
                    props = item.get("props") or {} if item else {}
                    description = " ".join(str(props.get("description") or "").split())
                    if not description:
                        continue
                    invoice_ids = set(invoice_ids)
                    if requested_customer_ids:
                        invoice_ids = {invoice_id for invoice_id in invoice_ids
                            if customers_for_invoice.get(invoice_id, set()) & requested_customer_ids}
                        if not invoice_ids:
                            continue
                    linked_customers = {customer_id for invoice_id in invoice_ids
                                        for customer_id in customers_for_invoice.get(invoice_id, set())}
                    rows.append((description, item_id, invoice_ids, linked_customers))
                counts = {}
                for description, item_id, invoice_ids, customer_ids in rows:
                    key = re.sub(r"\s+", " ", description).strip().casefold()
                    entry = counts.setdefault(key, {"description": description, "items": [],
                        "invoices": set(), "customers": set()})
                    entry["items"].append(item_id)
                    entry["invoices"].update(invoice_ids)
                    entry["customers"].update(customer_ids)
                ranked = sorted(counts.values(), key=lambda item: (-len(item["invoices"]),
                                 item["description"].casefold()))
                shared_items = [item for item in ranked if len(item["invoices"]) >= 2]
                review_scope = "included" if include_unreviewed else "reviewed"
                scope = (f"for {requested_customers[0]['props']['name']}" if requested_customers
                         else f"across {review_scope} invoices")
                invoice_ids_in_scope = {node_id for node_id, node in nodes.items()
                    if node.get("type") == "Invoice" and (not requested_customer_ids
                    or customers_for_invoice.get(node_id, set()) & requested_customer_ids)}
                invoice_count = len(invoice_ids_in_scope)
                def describe_product(item):
                    customer_names = [str((nodes.get(customer_id, {}).get("props") or {}).get("name"))
                                      for customer_id in sorted(item["customers"])
                                      if (nodes.get(customer_id, {}).get("props") or {}).get("name")]
                    invoice_numbers = [str((nodes.get(invoice_id, {}).get("props") or {}).get("invoice_number"))
                                       for invoice_id in sorted(item["invoices"])
                                       if (nodes.get(invoice_id, {}).get("props") or {}).get("invoice_number")]
                    links = []
                    if customer_names:
                        shown_customers = ", ".join(customer_names[:5])
                        if len(customer_names) > 5:
                            shown_customers += f" and {len(customer_names) - 5} more"
                        links.append("customers " + shown_customers)
                    if invoice_numbers:
                        shown_invoices = ", ".join(invoice_numbers[:8])
                        if len(invoice_numbers) > 8:
                            shown_invoices += f" and {len(invoice_numbers) - 8} more"
                        links.append("invoices " + shown_invoices)
                    suffix = "; " + "; ".join(links) if links else ""
                    invoice_pair_count = len(item["invoices"]) * (len(item["invoices"]) - 1) // 2
                    return (f"{item['description']} ({len(item['invoices'])} invoices; "
                            f"{invoice_pair_count} pair links{suffix})")
                pair_items = {}
                for item in shared_items:
                    invoice_ids = sorted(item["invoices"])
                    for index, left_id in enumerate(invoice_ids):
                        for right_id in invoice_ids[index + 1:]:
                            pair_items.setdefault((left_id, right_id), set()).add(item["description"])
                pair_count = sum(len(item["invoices"]) * (len(item["invoices"]) - 1) // 2
                                 for item in shared_items)
                pair_examples = sorted(pair_items.items(), key=lambda row: row[0])
                table_rows = []
                for item in shared_items:
                    item_invoice_count = len(item["invoices"])
                    item_invoice_pair_count = item_invoice_count * (item_invoice_count - 1) // 2
                    item_invoice_numbers = [str((nodes.get(invoice_id, {}).get("props") or {})
                        .get("invoice_number") or invoice_id) for invoice_id in sorted(item["invoices"])]
                    invoice_sample = ", ".join(item_invoice_numbers[:10])
                    if len(item_invoice_numbers) > 10:
                        invoice_sample += f" and {len(item_invoice_numbers) - 10} more"
                    table_rows.extend([
                        {"category": "Shared item", "entity": item["description"],
                         "property": "Distinct invoices", "value": str(item_invoice_count),
                         "confidence": None, "source": invoice_sample},
                        {"category": "Invoice item match", "entity": item["description"],
                         "property": "Invoice-pair links", "value": str(item_invoice_pair_count),
                         "confidence": None, "source": "Exact normalized item-description match"},
                    ])
                table_rows.extend({"category": "Invoice item match",
                    "entity": (f"{(nodes.get(left_id, {}).get('props') or {}).get('invoice_number') or left_id} ↔ "
                               f"{(nodes.get(right_id, {}).get('props') or {}).get('invoice_number') or right_id}"),
                    "property": "Shared item descriptions", "value": "; ".join(sorted(descriptions)),
                    "confidence": None, "source": "Exact normalized item-description match"}
                    for (left_id, right_id), descriptions in pair_examples[:500])
                if not ranked:
                    answer = (f"I couldn't find invoice line-item descriptions {scope}. "
                        "I excluded email summaries from this item comparison.")
                    selected_ids, links = [], []
                elif not shared_items:
                    answer = (f"I checked {len(rows)} described line items across {invoice_count} "
                        f"{review_scope} invoices {scope}, but found no exact item descriptions shared "
                        "by two or more invoices. So there are no item-match invoice links to count.")
                    selected_ids, links = [], []
                else:
                    checked = (f"I checked {len(rows)} described line items across {invoice_count} "
                               f"{review_scope} invoices {scope}")
                    examples = "\n".join(f"- {describe_product(item)}" for item in shared_items[:20])
                    more_note = (f"\nThe table contains all {len(shared_items)} shared item descriptions."
                                 if len(shared_items) > 20 else "")
                    pair_lines = []
                    for (left_id, right_id), descriptions in pair_examples[:20]:
                        left = (nodes.get(left_id, {}).get("props") or {}).get("invoice_number") or left_id
                        right = (nodes.get(right_id, {}).get("props") or {}).get("invoice_number") or right_id
                        pair_lines.append(f"- Invoice {left} ↔ Invoice {right}: {', '.join(sorted(descriptions))}")
                    pair_text = "\n".join(pair_lines)
                    answer = (f"{checked}. I found {len(shared_items)} exact item descriptions shared "
                        f"between invoices, creating {pair_count} item-match invoice links.\n"
                        f"Shared items (counted by distinct invoices):\n{examples}{more_note}\n"
                        f"Invoice-pair examples ({min(len(pair_examples), 20)} of {pair_count} links):\n"
                        f"{pair_text}\n"
                        "A pair is linked when its invoices contain the same item description after "
                        "case and whitespace normalization; this does not prove the products are identical.")
                    # Keep cited records and visible graph evidence bounded while making the
                    # full item counts available in the answer table.
                    selected_ids, links = [], []
                    direct_item_relations = [edge for edge in graph_export.get("edges", [])
                        if edge.get("type") == "SAME_ITEM_DESCRIPTION_AS"]
                    supporting_item_edges = {edge.get("id"): edge
                        for edge in graph_export.get("edges", [])
                        if edge.get("type") == "MATCHES_DESCRIPTION"}
                    for (left_id, right_id), descriptions in pair_examples:
                        if len(selected_ids) >= 24:
                            break
                        for description in sorted(descriptions):
                            normalized = re.sub(r"\s+", " ", description).strip().casefold()
                            relation = next((edge for edge in direct_item_relations
                                if re.sub(r"\s+", " ", str((edge.get("props") or {}).get(
                                    "shared_description") or "")).strip().casefold() == normalized
                                and {edge.get("source"), edge.get("target")} == {left_id, right_id}), None)
                            if not relation:
                                continue
                            match_edges = [supporting_item_edges[fact_id] for fact_id in
                                (relation.get("props") or {}).get("supporting_fact_ids", [])
                                if fact_id in supporting_item_edges]
                            item_ids = list(dict.fromkeys(edge.get("source") for edge in match_edges))
                            contains_ids = [edge_id for item_id in item_ids
                                for invoice_id, edge_id in item_edges_for_invoice.get(item_id, set())
                                if invoice_id in {left_id, right_id}]
                            hub_ids = [edge.get("target") for edge in match_edges if edge.get("target")]
                            source_ids = list(dict.fromkeys([left_id, right_id, *item_ids, *hub_ids]))
                            selected_ids.extend(source_ids)
                            edge_ids = list(dict.fromkeys([relation["id"],
                                *(edge.get("id") for edge in match_edges), *contains_ids]))
                            links.append({"id": relation["id"], "type": relation["type"],
                                "text": f"Invoice pair shares item description: {description}",
                                "score": 1.0, "source_tool": "sqlite_item_match",
                                "source_ids": source_ids, "edge_ids": edge_ids})
                    selected_ids = list(dict.fromkeys(selected_ids))[:24]
                    links = links[:24]
                sources = []
                for item_id in selected_ids:
                    if item_id not in nodes:
                        continue
                    source_node = nodes[item_id]
                    source_props = source_node.get("props") or {}
                    source_type = source_node.get("type")
                    source = {"id": item_id, "type": source_type,
                        "invoice_number": source_props.get("invoice_number"),
                        "filename": source_props.get("filename"), "data": source_props}
                    if source_type == "LineItem":
                        source["source"] = f"Item: {source_props.get('description') or 'description unavailable'}"
                        invoice_id = next(iter(invoices_for_item.get(item_id, set())), None)
                        invoice_number = (nodes.get(invoice_id, {}).get("props") or {}).get("invoice_number")
                        if invoice_number:
                            source["quote"] = f"Listed on invoice {invoice_number}."
                    elif source_type == "ItemDescription":
                        source["source"] = ("Shared item description: " +
                            str(source_props.get("normalized_description") or item_id))
                    sources.append(source)
                self._update_agent_run(request_id, {"status": "completed", "stage": "completed",
                    "event": {"stage": "product_evidence", "event": {"step": "product_evidence",
                        "status": "completed", "line_item_count": len(rows)}}})
                return {"answer": answer, "sources": sources, "links": links,
                    "table": table_rows, "retrieval_count": len(links) or len(sources),
                    "reviewed_only": not include_unreviewed,
                    "answer_mode": "agent_invoice_line_item_evidence",
                    "retrieval_backend": "sqlite+kg_agent", "confidence": None,
                    "citations": [source["id"] for source in sources],
                    "pipeline_steps": [{"step": 1, "name": "Invoice line-item search",
                        "description": "Counted invoice item descriptions and followed their pairwise item-match links.",
                        "detail": (f"{len(rows)} described line items across {invoice_count} {review_scope} invoices; "
                                   f"{len(shared_items)} shared descriptions and {pair_count if shared_items else 0} pairwise item matches; email text excluded.")}],
                    "request_id": request_id}
            try:
                result = run_papertrail_agent(graph_export, question, generate=generate,
                    history=history, on_progress=lambda event: self._update_agent_run(
                        request_id, {"status": "running", "stage": event.get("stage"),
                                     "event": event}))
            except Exception as exc:
                self._update_agent_run(request_id, {"status": "failed", "stage": "failed",
                                                    "error": type(exc).__name__})
                raise
            if used_unreviewed_product_fallback:
                result["answer"] = ("These matching invoice records still need review.\n\n"
                                     + result.get("answer", ""))
            self._update_agent_run(request_id, {"status": "completed", "stage": "completed"})
            node_export = {node["id"]: node for node in graph_export.get("nodes", [])}
            from ..agent_evidence import build_evidence_cards
            evidence_cards = build_evidence_cards(result.get("evidence", []), node_export,
                                                   graph_export.get("edges", []))
            sources = {}
            links = []
            for evidence in result.get("evidence", []):
                source_ids = list(evidence.get("node_ids", []))
                if evidence.get("doc_id") in node_export:
                    source_ids.insert(0, evidence["doc_id"])
                for node_id in source_ids:
                    node = node_export.get(node_id)
                    if not node:
                        continue
                    props = node.get("props") or {}
                    sources[node_id] = {"id": node_id, "type": node.get("type"),
                        "filename": props.get("filename") or props.get("source_file"),
                        "invoice_number": props.get("invoice_number"), "data": props}
                links.append({"id": evidence.get("id"), "type": evidence.get("kind"),
                    "text": evidence.get("text"), "score": evidence.get("score"),
                    "source_tool": evidence.get("source_tool"),
                    "source_ids": source_ids,
                    "edge_ids": evidence.get("edge_ids", []),
                    "citation": evidence.get("citation_id")})
            trace = result.get("trace", {})
            pipeline = []
            descriptions = {
                "query_transform": "Classified the question and selected retrieval tools.",
                "retrieval": "Collected candidate facts from the selected tools.",
                "rank_fusion": "Combined ranked results with reciprocal rank fusion.",
                "evidence_reranking": "Reranked candidates and kept the strongest evidence.",
                "evidence_packet": "Prepared bounded evidence with citation IDs.",
                "answer_generation": "Generated an answer from the evidence packet.",
                "answer_verification": "Checked answer support and completeness.",
            }
            for step in trace.get("steps", []):
                name = step.get("step", "agent_step")
                if name == "retrieve_answer_verify":
                    continue
                detail = {key: value for key, value in step.items() if key != "step"}
                pipeline.append({"step": len(pipeline) + 1,
                    "name": name.replace("_", " ").title(),
                    "description": descriptions.get(name, "Agent workflow step"),
                    "detail": json.dumps(detail, ensure_ascii=False, default=str)})
            return {"answer": result.get("answer"), "sources": list(sources.values()),
                "evidence_cards": evidence_cards,
                "links": links, "retrieval_count": len(links),
                "reviewed_only": not include_unreviewed and not used_unreviewed_product_fallback,
                "answer_mode": f"kg_agent_{result.get('model_provider', 'none')}" if generate
                               else "kg_agent_deterministic",
                "model_name": result.get("model_name"),
                "retrieval_backend": "sqlite+kg_agent", "confidence": result.get("confidence"),
                "citations": result.get("citations", []), "agent_trace": trace,
                "query_plan": result.get("query_plan"), "pipeline_steps": pipeline,
                "request_id": request_id}
        normalized_question = " ".join(question.casefold().split())
        if "why" in normalized_question and any(
                term in normalized_question for term in ("connect", "connection", "linked")):
            from ..graph_rag import explain_invoice_connections
            result = explain_invoice_connections(self.graph, question, include_unreviewed)
            with self._graph_llm_lock:
                now = time.monotonic()
                while self._graph_llm_calls and now - self._graph_llm_calls[0] >= 3600:
                    self._graph_llm_calls.popleft()
                calls_used = len(self._graph_llm_calls)
                calls_remaining = max(0, self._graph_llm_limit - calls_used)
            return {"answer": result["answer"], "sources": result["sources"],
                    "retrieval_count": len(result["links"]),
                    "context_truncated": result["context_truncated"],
                    "reviewed_only": result["reviewed_only"],
                    "excluded_unreviewed": result["excluded_unreviewed"],
                    "answer_mode": "local_sqlite_deterministic",
                    "retrieval_backend": "sqlite",
                    "llm_calls_used": calls_used, "llm_calls_remaining": calls_remaining,
                    "llm_call_limit": self._graph_llm_limit,
                    "links": result["links"]}
        from ..graph_rag import (GraphAnswerProvider, Neo4jRetriever,
                                 SQLiteGraphRetriever, summarize_graph_facts)
        retrieval_backend = "neo4j" if self.neo4j_driver is not None else "sqlite"
        retriever = (Neo4jRetriever(self.neo4j_driver, self.neo4j_database)
                     if self.neo4j_driver is not None else SQLiteGraphRetriever(self.graph))
        retrieved = retriever.retrieve(question, limit=limit,
                                       reviewed_only=not include_unreviewed)
        if self.neo4j_driver is not None:
            # Keep synthetic demo threads available to RAG even before the optional
            # Neo4j projection has been refreshed from the SQLite source of truth.
            local_retrieved = SQLiteGraphRetriever(self.graph).retrieve(
                question, limit=limit, reviewed_only=not include_unreviewed)
            if any((match.get("node") or {}).get("synthetic") is True
                   for match in local_retrieved.get("matches", [])):
                retrieved = local_retrieved
                retrieval_backend = "sqlite+synthetic-demo"
        answer_cached = False
        budget_fallback = False
        provider_mode = os.environ.get("GRAPH_RAG_BACKEND", "anthropic").strip().lower()
        if provider_mode not in ("anthropic", "ollama"):
            provider_mode = "hosted"
        answer_model = (os.environ.get("GRAPH_RAG_MODEL") or
                        os.environ.get("OLLAMA_MODEL", "") if provider_mode == "ollama" else
                        os.environ.get("GRAPH_RAG_MODEL") or
                        os.environ.get("VISION_API_MODEL", ""))
        if generate and retrieved.get("matches"):
            cache_matches = []
            for match in retrieved["matches"]:
                canonical = dict(match)
                canonical["links"] = sorted(match.get("links") or [],
                                             key=lambda link: link.get("relationship_id") or "")
                cache_matches.append(canonical)
            cache_payload = json.dumps({"question": " ".join(question.split()).casefold(),
                                        "matches": cache_matches,
                                        "sources": retrieved["sources"],
                                        "reviewed_only": retrieved["reviewed_only"],
                                        "provider": provider_mode, "model": answer_model},
                                       sort_keys=True, ensure_ascii=False, default=str)
            cache_key = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()
            with self._graph_llm_lock:
                answer = self._graph_answer_cache.get(cache_key)
                if answer is not None:
                    self._graph_answer_cache.move_to_end(cache_key)
                    answer_cached = True
            if answer is None:
                now = time.monotonic()
                with self._graph_llm_lock:
                    while self._graph_llm_calls and now - self._graph_llm_calls[0] >= 3600:
                        self._graph_llm_calls.popleft()
                    budget_exhausted = (self._graph_llm_limit == 0 or
                                        len(self._graph_llm_calls) >= self._graph_llm_limit)
                if budget_exhausted:
                    answer = summarize_graph_facts(retrieved)
                    budget_fallback = True
                else:
                    settings = self.settings
                    if settings is None:
                        from ..config import Settings
                        settings = Settings.from_env()
                    try:
                        factory = self.answer_provider_factory or GraphAnswerProvider
                        provider = factory(settings)
                    except RuntimeError as exc:
                        raise ReviewError(HTTPStatus.SERVICE_UNAVAILABLE, str(exc)) from exc
                    with self._graph_llm_lock:
                        now = time.monotonic()
                        while self._graph_llm_calls and now - self._graph_llm_calls[0] >= 3600:
                            self._graph_llm_calls.popleft()
                        if self._graph_llm_limit == 0 or len(self._graph_llm_calls) >= self._graph_llm_limit:
                            budget_exhausted = True
                        else:
                            self._graph_llm_calls.append(now)
                    if budget_exhausted:
                        answer = summarize_graph_facts(retrieved)
                        budget_fallback = True
                    else:
                        try:
                            answer = provider.answer(question, retrieved)
                        except RuntimeError as exc:
                            raise ReviewError(HTTPStatus.SERVICE_UNAVAILABLE, str(exc)) from exc
                        with self._graph_llm_lock:
                            self._graph_answer_cache[cache_key] = answer
                            self._graph_answer_cache.move_to_end(cache_key)
                            while len(self._graph_answer_cache) > 64:
                                self._graph_answer_cache.popitem(last=False)
                        retrieved["context_truncated"] = (retrieved.get("context_truncated", False)
                                                           or getattr(provider, "context_truncated", False))
        else:
            answer = summarize_graph_facts(retrieved)
        with self._graph_llm_lock:
            now = time.monotonic()
            while self._graph_llm_calls and now - self._graph_llm_calls[0] >= 3600:
                self._graph_llm_calls.popleft()
            calls_used = len(self._graph_llm_calls)
            calls_remaining = max(0, self._graph_llm_limit - calls_used)
        answer_mode = (f"{provider_mode}_cached" if answer_cached else
                       "local_budget_fallback" if budget_fallback else
                       provider_mode if generate and retrieved.get("matches") else "local")
        return {"answer": answer, "sources": retrieved["sources"],
                "retrieval_count": len(retrieved["matches"]),
                "context_truncated": bool(retrieved.get("context_truncated")),
                "reviewed_only": retrieved["reviewed_only"],
                "answer_mode": answer_mode,
                "retrieval_backend": retrieval_backend,
                "llm_calls_used": calls_used, "llm_calls_remaining": calls_remaining,
                "llm_call_limit": self._graph_llm_limit}

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


def make_handler(service: ReviewService, token: str, allowed_hosts=None):
    allowed_hosts = {host.lower() for host in (allowed_hosts or LOOPBACK_HOSTS)}
    class Handler(BaseHTTPRequestHandler):
        server_version = "PaperTrailReview"
        sys_version = ""
        conversation_log_lock = threading.Lock()

        def log_message(self, fmt, *args):  # keep request logs free of tokens and data
            pass

        # -- plumbing ----------------------------------------------------------------

        def _send(self, status, body: bytes, content_type: str, cache: str = "no-store",
                  extra_headers: dict = None):
            self.send_response(status)
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _serve_frontend(self, route: str):
            root = FRONTEND_DIST_DIR.resolve()
            relative = unquote(route).lstrip("/") or "index.html"
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            if not target.is_file():
                # BrowserRouter routes such as /ask and /graph need the SPA entry point.
                if Path(relative).suffix:
                    return self._error(HTTPStatus.NOT_FOUND, "Not found")
                target = root / "index.html"
            body = target.read_bytes()
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if kind.startswith("text/") or kind in {"application/javascript", "image/svg+xml"}:
                kind += "; charset=utf-8"
            cache = ("public, max-age=31536000, immutable"
                     if "/assets/" in route else "no-store")
            return self._send(HTTPStatus.OK, body, kind, cache)

        def _json(self, status, value):
            body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
            headers = {}
            if len(body) >= 8192 and "gzip" in (self.headers.get("Accept-Encoding") or "").lower():
                body = gzip.compress(body, compresslevel=4)
                headers = {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
            self._send(status, body, "application/json; charset=utf-8", extra_headers=headers)

        def _error(self, status, message):
            self._json(status, {"error": message})

        def _append_agent_conversation(self, question, history, response, conversation_id=None):
            """Persist one local agent turn as a compact JSONL record."""
            log_path = Path(os.environ.get(
                "PAPERTRAIL_CONVERSATION_LOG", "runs/agent_conversations.jsonl"))
            answer = str(response.get("answer") or "")
            record = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "conversation_id": conversation_id,
                "request_id": response.get("request_id"),
                "question": question,
                "history": [{"role": item.get("role"),
                             "content": str(item.get("content") or "")[:1200]}
                            for item in (history or [])[-8:] if isinstance(item, dict)],
                "answer": answer,
                "model": response.get("model_name"),
                "confidence": response.get("confidence"),
                "citations": response.get("citations") or [],
                "sources": [{"id": source.get("id"), "type": source.get("type"),
                             "invoice_number": source.get("invoice_number"),
                             "filename": source.get("filename")}
                            for source in response.get("sources", []) if isinstance(source, dict)],
            }
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.conversation_log_lock:
                    with log_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            except OSError:
                # A log path or disk issue must not turn a completed answer into a failed query.
                pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip()
            name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
            return name.lower() in allowed_hosts

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
            if not route.startswith("/api/") and (FRONTEND_DIST_DIR / "index.html").is_file():
                if not self._guard(api=False):
                    return
                return self._serve_frontend(route)
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
                if parts == ["analytics"]:
                    return self._json(HTTPStatus.OK, service.analytics())
                if parts == ["evidence"]:
                    return self._json(HTTPStatus.OK, service.evidence())
                if parts == ["status"]:
                    return self._json(HTTPStatus.OK, service.runtime_status())
                if parts == ["settings"]:
                    return self._json(HTTPStatus.OK, service.runtime_status())
                if parts == ["semantic-model"]:
                    return self._json(HTTPStatus.OK, service.semantic_model())
                if parts == ["contradictions"]:
                    return self._json(HTTPStatus.OK, service.potential_conflicts())
                if parts == ["search"]:
                    term = query.get("q", [""])[0]
                    if len(term) > 200:
                        return self._error(HTTPStatus.BAD_REQUEST, "Search query is too long")
                    return self._json(HTTPStatus.OK, service.search(term))
                if parts == ["conversations"]:
                    return self._json(HTTPStatus.OK, service.conversation_threads())
                if parts == ["graph"]:
                    reviewed = query.get("reviewed_only", ["0"])[0] in ("1", "true")
                    return self._json(HTTPStatus.OK, service.graph_export(reviewed))
                if parts == ["neo4j", "graph"]:
                    reviewed = query.get("reviewed_only", ["0"])[0] in ("1", "true")
                    return self._json(HTTPStatus.OK, service.neo4j_graph_export(reviewed))
                if parts == ["changes"]:
                    return self._json(HTTPStatus.OK, service.graph.changes())
                if len(parts) == 2 and parts[0] == "agent-runs":
                    try:
                        request_id = str(uuid.UUID(parts[1]))
                    except ValueError:
                        return self._error(HTTPStatus.BAD_REQUEST, "Invalid agent run ID")
                    state = service.agent_run(request_id)
                    if state is None:
                        return self._error(HTTPStatus.NOT_FOUND, "Agent run not found")
                    return self._json(HTTPStatus.OK, state)
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
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._error(HTTPStatus.BAD_REQUEST, "Bad Content-Length")
            from ..config import MAX_FILE_BYTES
            body_limit = MAX_FILE_BYTES + 1024 * 1024 if route == "/api/upload" else MAX_BODY_BYTES
            if length > body_limit:
                # Drain a bounded oversized request so the client can receive the 413.
                remaining = length if length <= body_limit else 0
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self.close_connection = True
                return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Upload exceeds the configured file limit")
            body = self.rfile.read(length)
            if route == "/api/upload":
                if not content_type.startswith("multipart/form-data"):
                    return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Upload files using multipart/form-data")
                from ..config import SUPPORTED_SUFFIXES, WATCH_FOLDER
                try:
                    envelope = (f"Content-Type: {self.headers.get('Content-Type')}\r\n"
                                "MIME-Version: 1.0\r\n\r\n").encode("utf-8")
                    message = BytesParser(policy=email_policy).parsebytes(envelope + body)
                    uploaded = next((part for part in message.iter_parts()
                                     if part.get_filename()), None)
                    if uploaded is None:
                        return self._error(HTTPStatus.BAD_REQUEST, "Choose a file to upload")
                    original_name = uploaded.get_filename().replace("\\", "/")
                    filename = Path(original_name).name.strip()
                    suffix = Path(filename).suffix.lower()
                    data = uploaded.get_payload(decode=True) or b""
                    if not filename or filename in {".", ".."}:
                        return self._error(HTTPStatus.BAD_REQUEST, "Invalid filename")
                    if suffix not in SUPPORTED_SUFFIXES:
                        return self._error(HTTPStatus.BAD_REQUEST,
                                           f"Unsupported file type: {suffix or '(no extension)'}")
                    if not data or len(data) > MAX_FILE_BYTES:
                        return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                           "File is empty or exceeds the configured per-file limit")
                    WATCH_FOLDER.mkdir(parents=True, exist_ok=True)
                    destination = WATCH_FOLDER / filename
                    stem, extension = destination.stem, destination.suffix
                    version = 1
                    while destination.exists():
                        destination = WATCH_FOLDER / f"{stem} ({version}){extension}"
                        version += 1
                    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.uploading")
                    temporary.write_bytes(data)
                    temporary.replace(destination)
                    return self._json(HTTPStatus.CREATED, {
                        "filename": destination.name, "watch_folder": str(WATCH_FOLDER),
                        "bytes": len(data), "message": "Stored in watched folder; the watcher will register it."})
                except (ValueError, OSError) as exc:
                    return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            if content_type != "application/json":
                return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send application/json")
            try:
                payload = json.loads(body.decode("utf-8") or "null")
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
                    mode = payload.get("mode", "hybrid")
                    history = payload.get("history")
                    conversation_id = payload.get("conversation_id")
                    request_id = payload.get("request_id")
                    if not isinstance(generate, bool) or not isinstance(include_unreviewed, bool):
                        return self._error(HTTPStatus.BAD_REQUEST,
                                           "generate and include_unreviewed must be boolean")
                    if not isinstance(limit, int) or isinstance(limit, bool):
                        return self._error(HTTPStatus.BAD_REQUEST, "limit must be an integer")
                    if mode not in ("agent", "hybrid"):
                        return self._error(HTTPStatus.BAD_REQUEST,
                                           "mode must be agent or hybrid")
                    if history is not None and (not isinstance(history, list) or len(history) > 8 or
                            any(not isinstance(item, dict) or item.get("role") not in ("user", "assistant")
                                or not isinstance(item.get("content"), str)
                                for item in history)):
                        return self._error(HTTPStatus.BAD_REQUEST,
                                           "history must contain at most 8 user or assistant messages")
                    if conversation_id is not None and (not isinstance(conversation_id, str)
                            or len(conversation_id) > 100):
                        return self._error(HTTPStatus.BAD_REQUEST, "conversation_id must be short text")
                    if request_id is not None:
                        try:
                            request_id = str(uuid.UUID(request_id))
                        except (TypeError, ValueError, AttributeError):
                            return self._error(HTTPStatus.BAD_REQUEST, "request_id must be a UUID")
                    result = service.ask_graph(question, generate=generate,
                        include_unreviewed=include_unreviewed, limit=limit, mode=mode,
                        history=history, request_id=request_id)
                    if mode == "agent" and generate:
                        self._append_agent_conversation(question, history, result, conversation_id)
                    return self._json(HTTPStatus.OK, result)
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
                  token: str = None, allowed_hosts=None):
    if host not in ("127.0.0.1", "::1", "localhost") and not allowed_hosts:
        raise ValueError("A non-loopback bind requires an explicit host allow-list")
    token = token or secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((host, port), make_handler(service, token, allowed_hosts))
    server.daemon_threads = True
    server.review_token = token
    return server


def main(argv=None):
    import os
    from ..config import DATABASE_PATH, GRAPH_PATH, Settings
    from ..graph import GraphStore
    from ..ingestion.registry import DocumentRegistry

    build_frontend_if_needed()

    parser = argparse.ArgumentParser(description="Local Paper Trail review server")
    parser.add_argument("--port", type=int, default=int(os.getenv("REVIEW_PORT", "8765")))
    parser.add_argument("--host", default=os.getenv("REVIEW_HOST", "127.0.0.1"))
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    neo4j_driver = None
    if os.getenv("NEO4J_URI") and os.getenv("NEO4J_PASSWORD"):
        from ..neo4j_sync import create_driver
        neo4j_driver = create_driver()
    service = ReviewService(GraphStore(GRAPH_PATH), DocumentRegistry(DATABASE_PATH), settings,
                            neo4j_driver=neo4j_driver,
                            neo4j_database=os.getenv("NEO4J_DATABASE") or None)
    watcher_stop = threading.Event()
    watcher_thread = None
    if os.getenv("PAPERTRAIL_ENABLE_WATCHER", "0").lower() in {"1", "true", "yes"}:
        from ..config import MAX_FILE_BYTES, POLL_INTERVAL_SECONDS, WATCH_FOLDER
        from ..ingestion.watcher import FolderWatcher
        from ..main import build_processor
        service.registry.recover_interrupted_jobs()
        folder_watcher = FolderWatcher(WATCH_FOLDER, service.registry, MAX_FILE_BYTES)
        processor = build_processor(service.registry, settings)

        def watch_and_process():
            while not watcher_stop.is_set():
                folder_watcher.scan_once()
                if processor:
                    processor.process_pending()
                watcher_stop.wait(POLL_INTERVAL_SECONDS)

        watcher_thread = threading.Thread(target=watch_and_process, name="papertrail-watcher", daemon=True)
        watcher_thread.start()
    allowed_hosts = {item.strip().lower() for item in os.getenv("PAPERTRAIL_PUBLIC_HOSTS", "").split(",") if item.strip()}
    render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip().lower()
    if render_hostname:
        allowed_hosts.add(render_hostname)
    if args.host not in ("127.0.0.1", "::1", "localhost") and not allowed_hosts:
        raise SystemExit("Public binding requires a host allow-list or the platform hostname.")
    if allowed_hosts:
        allowed_hosts.update(LOOPBACK_HOSTS)
    server = create_server(service, args.host, args.port, os.getenv("REVIEW_TOKEN") or None,
                           allowed_hosts or None)
    host = f"[{args.host}]" if ":" in args.host else args.host
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print(f"Paper Trail is listening on {args.host}:{server.server_address[1]}.")
        print("Open the deployed HTTPS URL and add your configured REVIEW_TOKEN to its URL fragment.")
    else:
        print(f"Paper Trail UI: http://{host}:{server.server_address[1]}/#token={server.review_token}")
        print("The React app is served at this address; keep the one-time URL private.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher_stop.set()
        if watcher_thread is not None:
            watcher_thread.join(timeout=10)
        server.server_close()
        if neo4j_driver is not None:
            neo4j_driver.close()


if __name__ == "__main__":
    main()
