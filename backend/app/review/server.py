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
                     "documents_processed_today": len(documents),
                     "entities_extracted": len(entities), "entities_today": len(entities),
                     "relationships_discovered": len(graph["edges"] + hub_edges + direct_edges),
                     "relationships_today": len(graph["edges"] + hub_edges + direct_edges),
                     "facts_extracted": fact_count, "facts_today": fact_count},
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
                                     "rejected_hallucinations": 0},
        }

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
            from ..agentic_rag import run_papertrail_agent

            request_id = request_id or secrets.token_hex(16)
            self._update_agent_run(request_id, {"status": "running", "stage": "accepted"})
            graph_export = self.graph_export(reviewed_only=not include_unreviewed)
            if not include_unreviewed:
                all_export = self.graph.export(reviewed_only=False)
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
                ("product", "products", "service", "services", "line item", "line-item"))
                and any(term in question_lower for term in
                ("most often", "most common", "appear", "frequent", "across")))
            if asks_product_frequency:
                nodes = {node["id"]: node for node in graph_export.get("nodes", [])}
                invoices_for_item = {}
                customers_for_invoice = {}
                for edge in graph_export.get("edges", []):
                    if edge.get("type") == "CONTAINS":
                        invoice_id, item_id = edge.get("source"), edge.get("target")
                        if nodes.get(invoice_id, {}).get("type") == "LineItem":
                            invoice_id, item_id = item_id, invoice_id
                        if nodes.get(invoice_id, {}).get("type") == "Invoice":
                            invoices_for_item.setdefault(item_id, set()).add(invoice_id)
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
                    linked_customers = {customer_id for invoice_id in invoice_ids
                                        for customer_id in customers_for_invoice.get(invoice_id, set())}
                    if requested_customer_ids and not linked_customers.intersection(requested_customer_ids):
                        continue
                    rows.append((description, item_id, invoice_ids, linked_customers))
                counts = {}
                for description, item_id, invoice_ids, customer_ids in rows:
                    key = re.sub(r"\W+", " ", description.casefold()).strip()
                    entry = counts.setdefault(key, {"description": description, "items": [],
                        "invoices": set(), "customers": set()})
                    entry["items"].append(item_id)
                    entry["invoices"].update(invoice_ids)
                    entry["customers"].update(customer_ids)
                ranked = sorted(counts.values(), key=lambda item: (-len(item["items"]),
                                 item["description"].casefold()))
                scope = (f"for {requested_customers[0]['props']['name']}" if requested_customers
                         else "across reviewed invoices")
                invoice_count = len({invoice_id for row in rows for invoice_id in row[2]})
                def describe_product(item):
                    customer_names = [str((nodes.get(customer_id, {}).get("props") or {}).get("name"))
                                      for customer_id in sorted(item["customers"])
                                      if (nodes.get(customer_id, {}).get("props") or {}).get("name")]
                    invoice_numbers = [str((nodes.get(invoice_id, {}).get("props") or {}).get("invoice_number"))
                                       for invoice_id in sorted(item["invoices"])
                                       if (nodes.get(invoice_id, {}).get("props") or {}).get("invoice_number")]
                    links = []
                    if customer_names:
                        links.append("customer " + ", ".join(customer_names))
                    if invoice_numbers:
                        links.append("invoice " + ", ".join(invoice_numbers))
                    suffix = "; " + "; ".join(links) if links else ""
                    return f"{item['description']} ({len(item['items'])} record{'' if len(item['items']) == 1 else 's'}{suffix})"
                if not ranked:
                    answer = f"I couldn't find reviewed invoice line-item descriptions {scope}. I excluded email summaries from this product search."
                    selected_ids = []
                else:
                    checked = (f"I checked {len(rows)} line-item records {scope}"
                               if requested_customers else
                               f"I checked {len(rows)} line-item records across {invoice_count} reviewed invoices")
                    if len(ranked[0]["items"]) > 1:
                        leaders = [item for item in ranked if len(item["items"]) == len(ranked[0]["items"])][:5]
                        labels = "\n".join(f"- {describe_product(item)}" for item in leaders)
                        answer = (f"{checked}. These are the most frequently listed products:\n{labels}\n"
                                  "I counted invoice line items only, not email wording.")
                    else:
                        examples = "\n".join(f"- {describe_product(item)}" for item in ranked[:5])
                        answer = (f"{checked}. I didn't find a product description that repeats, "
                                  "so there isn't a single most common product in this set. "
                                  f"Here are a few examples:\n{examples}\n"
                                  "I counted invoice line items only, not email wording.")
                    selected_ids = list(dict.fromkeys(
                        [item_id for item in ranked[:5] for item_id in item["items"]] +
                        [invoice_id for item in ranked[:5] for invoice_id in item["invoices"]] +
                        [customer_id for item in ranked[:5] for customer_id in item["customers"]]))
                sources = [{"id": item_id, "type": nodes[item_id].get("type"),
                            "invoice_number": (nodes[item_id].get("props") or {}).get("invoice_number"),
                            "filename": (nodes[item_id].get("props") or {}).get("filename"),
                            "data": nodes[item_id].get("props") or {}}
                           for item_id in selected_ids if item_id in nodes]
                self._update_agent_run(request_id, {"status": "completed", "stage": "completed",
                    "event": {"stage": "product_evidence", "event": {"step": "product_evidence",
                        "status": "completed", "line_item_count": len(rows)}}})
                return {"answer": answer, "sources": sources, "links": [],
                    "retrieval_count": len(sources), "reviewed_only": True,
                    "answer_mode": "agent_invoice_line_item_evidence",
                    "retrieval_backend": "sqlite+kg_agent", "confidence": None,
                    "citations": [source["id"] for source in sources],
                    "pipeline_steps": [{"step": 1, "name": "Invoice line-item search",
                        "description": "Counted reviewed invoice line-item descriptions and followed their invoice/customer links.",
                        "detail": f"{len(rows)} line items; email text excluded."}],
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
                "links": links, "retrieval_count": len(links), "reviewed_only": not include_unreviewed,
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


def make_handler(service: ReviewService, token: str):
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
            body = self.rfile.read(length)
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

    build_frontend_if_needed()

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
    print(f"Paper Trail UI: http://{host}:{server.server_address[1]}/#token={server.review_token}")
    print("The React app is served at this address; keep the one-time URL private.")
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
