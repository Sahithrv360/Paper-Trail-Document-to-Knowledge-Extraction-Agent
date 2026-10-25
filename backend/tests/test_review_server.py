"""Milestone 3: local review server (HTTP layer, security guards, review flow)."""

import http.client
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.app.extraction.providers import FakeProvider
from backend.app.graph import GraphStore
from backend.app.ingestion.registry import DocumentRegistry
from backend.app.ingestion.watcher import FolderWatcher
from backend.app.pipeline import JobProcessor
from backend.app.review.server import ReviewService, create_server
from test_invoice_242 import INVOICE_242

FIXTURE = Path(__file__).parent / "fixtures" / "invoice-242.jpg"
TOKEN = "test-token-123"


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow not installed (image loading)")
class ReviewServerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        watched = root / "watched"
        watched.mkdir()
        (watched / "invoice-242.jpg").write_bytes(FIXTURE.read_bytes())
        self.registry = DocumentRegistry(root / "r.sqlite3")
        self.graph = GraphStore(root / "g.sqlite3")
        watcher = FolderWatcher(watched, self.registry, 50_000_000)
        watcher.scan_once()
        watcher.scan_once()
        output = dict(INVOICE_242, bank_account_iban="GBO2TRCS19981598057841",
                      evidence={"invoice_number": {"quote": "Invoice no: 95537278", "page": 1,
                                                   "bbox": [200, 110, 700, 150]}})
        JobProcessor(self.registry, self.graph, FakeProvider([output])).process_pending()
        self.document_id = self.registry.documents()[0]["document_id"]
        self.watched = watched
        self.server = create_server(ReviewService(self.graph, self.registry), port=0, token=TOKEN)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None, token=TOKEN, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        all_headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            all_headers["X-Review-Token"] = token
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), data

    def json(self, method, path, body=None, **kwargs):
        status, headers, data = self.request(method, path, body, **kwargs)
        return status, json.loads(data) if data and headers.get("Content-Type", "").startswith("application/json") else data

    def test_static_page_is_served_with_strict_headers(self):
        status, headers, data = self.request("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn(b"Paper Trail", data)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(self.request("GET", "/app.js", token=None)[0], 200)

    def test_api_requires_token_loopback_host_and_same_origin(self):
        self.assertEqual(self.request("GET", "/api/documents", token=None)[0], 401)
        self.assertEqual(self.request("GET", "/api/documents", token="wrong")[0], 401)
        self.assertEqual(self.request("GET", "/api/documents", host="attacker.example")[0], 421)
        self.assertEqual(self.request("GET", "/api/documents",
                                      headers={"Origin": "http://attacker.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/documents")[0], 200)

    def test_document_detail_and_source_page(self):
        status, docs = self.json("GET", "/api/documents")
        self.assertEqual(status, 200)
        self.assertEqual(docs[0]["review_status"], "needs_review")
        status, detail = self.json("GET", f"/api/documents/{self.document_id}")
        self.assertEqual(detail["file_state"], "current")
        self.assertEqual(detail["extraction"]["record"]["invoice_number"], "95537278")
        self.assertEqual(detail["extraction"]["effective_record"]["field_evidence"]["invoice_number"]["bbox"],
                         [200, 110, 700, 150])
        status, headers, png = self.request("GET", f"/api/documents/{self.document_id}/pages/1.png")
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(self.request("GET", f"/api/documents/{self.document_id}/pages/2.png")[0], 404)

    def test_paths_from_the_request_never_reach_the_disk(self):
        for path in ("/api/documents/../../etc/passwd/pages/1.png", "/api/documents/x/pages/1.png",
                     "/api/documents/%2e%2e/pages/1.png", "/../backend/app/config.py",
                     "/api/documents/x/pages/..%2f1.png"):
            self.assertIn(self.request("GET", path)[0], (404,), path)

    def test_changed_source_file_is_not_shown_for_an_old_extraction(self):
        (self.watched / "invoice-242.jpg").write_bytes(b"different bytes")
        status, payload = self.json("GET", f"/api/documents/{self.document_id}/pages/1.png")
        self.assertEqual(status, 409)

    def test_write_requires_json_and_size_limit(self):
        path = f"/api/documents/{self.document_id}/review"
        status, _, _ = self.request("POST", path, body=b"decision=approved",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        status, _, _ = self.request("POST", path, body=b"x" * (1024 * 1024 + 10),
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(status, 413)
        self.assertEqual(self.request("POST", path, body=b"{bad",
                                      headers={"Content-Type": "application/json"})[0], 400)

    def test_approve_and_correct_through_the_api(self):
        path = f"/api/documents/{self.document_id}/review"
        _, detail = self.json("GET", f"/api/documents/{self.document_id}")
        extraction_id = detail["extraction"]["extraction_id"]
        status, payload = self.json("POST", path, {"decision": "approved", "reviewer": "x",
                                                   "expected_extraction_id": extraction_id + 1})
        self.assertEqual(status, 409)  # stale view: a different extraction is current
        corrected = dict(INVOICE_242, bank_account_iban="GB02TRCS19981598057841")
        status, payload = self.json("POST", path, {"decision": "corrected", "reviewer": "Sriram",
                                                   "note": "IBAN misread", "corrected_record": corrected,
                                                   "expected_extraction_id": extraction_id})
        self.assertEqual(status, 200)
        extraction = payload["document"]["extraction"]
        self.assertEqual(extraction["review_status"], "corrected")
        self.assertEqual(extraction["record"]["bank_account_iban"], "GBO2TRCS19981598057841")
        self.assertEqual(extraction["effective_record"]["bank_account_iban"], "GB02TRCS19981598057841")
        self.assertEqual(payload["document"]["versions"][0]["reviews"][0]["reviewer"], "Sriram")
        status, docs = self.json("GET", "/api/documents")
        self.assertEqual(docs[0]["review_status"], "corrected")
        status, graph = self.json("GET", "/api/graph?reviewed_only=1")
        self.assertEqual(len([n for n in graph["nodes"] if n["type"] == "Invoice"]), 1)
        status, changes = self.json("GET", "/api/changes")
        self.assertEqual([c["action"] for c in changes], ["create", "review"])
        self.assertEqual(changes[-1]["run_id"], "review-ui")

    def test_invalid_correction_is_rejected_without_change(self):
        path = f"/api/documents/{self.document_id}/review"
        _, detail = self.json("GET", f"/api/documents/{self.document_id}")
        status, payload = self.json("POST", path, {
            "decision": "corrected", "reviewer": "x", "corrected_record": {"items": "nope"},
            "expected_extraction_id": detail["extraction"]["extraction_id"]})
        self.assertEqual(status, 422)
        self.assertEqual(self.graph.get_extraction(self.document_id)["review_status"], "needs_review")

    def test_server_refuses_non_loopback_bind(self):
        with self.assertRaises(ValueError):
            create_server(ReviewService(self.graph), host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
