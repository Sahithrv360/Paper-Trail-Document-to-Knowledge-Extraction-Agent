"""Watcher job queue -> extraction -> graph synchronization."""

import tempfile
import unittest
from pathlib import Path

from backend.app.extraction.providers import FakeProvider
from backend.app.graph import GraphStore
from backend.app.ingestion.registry import DocumentRegistry
from backend.app.ingestion.watcher import FolderWatcher
from backend.app.main import run_cycle
from backend.app.pipeline import JobProcessor


def invoice(number="INV-1", vendor="Seller Inc", total=7.7):
    return {"invoice_number": number, "invoice_date": "01/02/2024",
            "customer": {"name": "Buyer LLC"}, "vendor": {"name": vendor, "tax_id": "S-9"},
            "items": [{"description": "Paper", "quantity": 2, "unit_price": 3.5, "amount": 7.0}],
            "subtotal": 7.0, "tax_amount": 0.7, "total_amount": total}


class JobPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "in"
        self.input.mkdir()
        self.registry = DocumentRegistry(self.root / "r.sqlite3")
        self.graph = GraphStore(self.root / "g.sqlite3")
        self.watcher = FolderWatcher(self.input, self.registry, max_file_bytes=10_000)

    def processor(self, outputs):
        self.provider = FakeProvider(outputs)
        return JobProcessor(self.registry, self.graph, self.provider,
                            image_loader=lambda path: object())

    def settle(self):
        self.watcher.scan_once()
        self.watcher.scan_once()

    def types(self, kind):
        return [n for n in self.graph.export()["nodes"] if n["type"] == kind]

    def doc_id(self, index=0):
        return self.registry.documents()[index]["document_id"]

    def test_queued_job_is_extracted_synced_and_marked_needs_review(self):
        (self.input / "a.png").write_bytes(b"one")
        self.settle()
        results = self.processor([invoice()]).process_pending()
        self.assertEqual([r["outcome"] for r in results], ["synced"])
        self.assertEqual(self.registry.documents()[0]["status"], "complete")
        stored = self.graph.get_extraction(self.doc_id())
        # Arithmetic checks pass here, yet the phase keeps the record in review.
        self.assertEqual(stored["warnings"], [])
        self.assertEqual(stored["record"]["review_status"], "needs_review")
        self.assertEqual(self.types("Invoice")[0]["props"]["review_status"], "needs_review")
        self.assertEqual({n["type"] for n in self.graph.export()["nodes"]},
                         {"Document", "Invoice", "Vendor", "Customer", "LineItem"})

    def test_provenance_names_document_version_hash_and_file_only(self):
        (self.input / "a.png").write_bytes(b"one")
        self.settle()
        self.processor([invoice()]).process_pending()
        prov = self.graph.get_extraction(self.doc_id())["record"]["provenance"]
        doc = self.registry.documents()[0]
        self.assertEqual((prov["document_id"], prov["version"], prov["content_hash"],
                          prov["source_file"], prov["provider"]),
                         (doc["document_id"], 1, doc["content_hash"], "a.png", "fake"))
        self.assertNotIn(str(self.root), str(prov))

    def test_duplicate_scans_and_reruns_do_not_duplicate_work(self):
        (self.input / "a.png").write_bytes(b"one")
        self.settle()
        proc = self.processor([invoice()])
        proc.process_pending()
        before = self.graph.export()
        self.settle()
        self.assertEqual(proc.process_pending(), [])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(before, self.graph.export())

    def test_explicit_reprocess_reruns_same_version_and_updates_graph(self):
        (self.input / "a.png").write_bytes(b"one")
        self.settle()
        self.processor([invoice("OLD")]).process_pending()
        document = self.registry.documents()[0]
        job_id = self.registry.enqueue_reprocess(document["document_id"])
        result = next(item for item in self.processor([invoice("REVISED")]).process_pending()
                      if item["job_id"] == job_id)
        self.assertEqual(result["outcome"], "synced")
        self.assertEqual(self.registry.documents()[0]["version"], 1)
        self.assertEqual(self.types("Invoice")[0]["props"]["invoice_number"], "REVISED")

    def test_malformed_output_retries_then_fails_and_graph_stays_empty(self):
        (self.input / "a.png").write_bytes(b"one")
        self.settle()
        results = self.processor(["not json"] * 3).process_pending()
        self.assertEqual([r["outcome"] for r in results], ["retry", "retry", "failed"])
        self.assertTrue(all(r["validation_status"] == "invalid_response" for r in results))
        self.assertEqual(self.registry.documents()[0]["status"], "failed")
        self.assertEqual(self.graph.export(), {"nodes": [], "edges": []})

    def test_failed_new_version_keeps_last_valid_graph_state(self):
        path = self.input / "a.png"
        path.write_bytes(b"v1")
        self.settle()
        self.processor([invoice("INV-1")]).process_pending()
        path.write_bytes(b"version two")
        self.settle()
        self.processor(["bad"] * 3).process_pending()
        self.assertEqual(self.types("Invoice")[0]["props"]["invoice_number"], "INV-1")

    def test_changed_file_replaces_that_documents_facts(self):
        path = self.input / "a.png"
        path.write_bytes(b"v1")
        self.settle()
        self.processor([invoice("INV-1")]).process_pending()
        path.write_bytes(b"version two")
        self.settle()
        self.processor([invoice("INV-1B")]).process_pending()
        self.assertEqual([n["props"]["invoice_number"] for n in self.types("Invoice")], ["INV-1B"])
        self.assertEqual(self.graph.get_extraction(self.doc_id())["record"]["provenance"]["version"], 2)

    def test_job_for_superseded_version_is_skipped(self):
        path = self.input / "a.png"
        path.write_bytes(b"v1")
        self.settle()
        path.write_bytes(b"version two")
        self.settle()  # both versions are queued before anything runs
        results = self.processor([invoice("INV-2")]).process_pending()
        self.assertEqual([r["outcome"] for r in results], ["skipped_superseded", "synced"])
        self.assertEqual(len(self.provider.calls), 1)

    def test_delete_removes_only_unshared_facts(self):
        (self.input / "a.png").write_bytes(b"one")
        (self.input / "b.png").write_bytes(b"two")
        self.settle()
        self.processor([invoice("INV-A"), invoice("INV-B")]).process_pending()
        self.assertEqual(len(self.types("Vendor")), 1)
        (self.input / "a.png").unlink()
        self.watcher.scan_once()
        self.processor([]).process_pending()
        self.assertEqual([n["props"]["invoice_number"] for n in self.types("Invoice")], ["INV-B"])
        self.assertEqual(len(self.types("Vendor")), 1)
        self.assertIsNone(self.graph.get_extraction(
            next(d["document_id"] for d in self.registry.documents() if d["path"].endswith("a.png"))))

    def test_delete_restore_delete_cycle_stays_consistent(self):
        path = self.input / "a.png"
        path.write_bytes(b"one")
        self.settle()
        self.processor([invoice()]).process_pending()
        path.unlink()
        self.watcher.scan_once()
        self.processor([]).process_pending()
        self.assertEqual(self.graph.export(), {"nodes": [], "edges": []})
        path.write_bytes(b"one")  # restored, unchanged content
        self.settle()
        self.processor([invoice()]).process_pending()
        self.assertEqual(len(self.types("Invoice")), 1)
        path.unlink()
        self.watcher.scan_once()
        self.processor([]).process_pending()
        self.assertEqual(self.graph.export(), {"nodes": [], "edges": []})

    def test_delete_job_is_skipped_if_file_was_restored_first(self):
        path = self.input / "a.png"
        path.write_bytes(b"one")
        self.settle()
        proc = self.processor([invoice(), invoice()])
        proc.process_pending()
        path.unlink()
        self.watcher.scan_once()  # delete job queued, not yet run
        path.write_bytes(b"one")
        self.settle()
        results = proc.process_pending()
        self.assertIn("skipped_restored", [r["outcome"] for r in results])
        self.assertEqual(len(self.types("Invoice")), 1)

    def test_unreadable_pdf_is_a_permanent_failure_without_retries(self):
        # PDFs are supported when the optional pypdfium2 extra is installed; a corrupt
        # PDF (or a missing extra) fails once, permanently, before the provider runs.
        (self.input / "a.pdf").write_bytes(b"pdf")
        self.settle()
        results = self.processor([]).process_pending()
        self.assertEqual([r["outcome"] for r in results], ["failed"])
        self.assertIn("PDF", results[0]["error"])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.graph.export(), {"nodes": [], "edges": []})

    def test_arithmetic_warnings_are_stored_for_the_reviewer(self):
        (self.input / "a.png").write_bytes(b"one")
        self.settle()
        self.processor([invoice(total=99.0)]).process_pending()
        stored = self.graph.get_extraction(self.doc_id())
        self.assertTrue(stored["warnings"])
        self.assertEqual(stored["record"]["total_amount"], 99.0)

    def test_run_cycle_without_processor_only_queues(self):
        (self.input / "a.png").write_bytes(b"one")
        run_cycle(self.watcher)
        self.assertEqual(run_cycle(self.watcher), [])
        self.assertEqual(self.registry.jobs()[0]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
