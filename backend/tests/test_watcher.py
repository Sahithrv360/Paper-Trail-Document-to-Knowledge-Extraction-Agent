"""Tests for stable folder ingestion and registry behavior."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.ingestion.registry import DocumentRegistry
from backend.app.ingestion.watcher import FolderWatcher


class FolderWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "invoices"
        self.registry = DocumentRegistry(self.root / "registry.sqlite3")
        self.watcher = FolderWatcher(self.input, self.registry, max_file_bytes=20)

    def tearDown(self):
        self.temp.cleanup()

    def test_startup_scan_waits_for_stable_file_then_queues_once(self):
        self.input.mkdir()
        invoice = self.input / "one.pdf"
        invoice.write_bytes(b"invoice")
        self.watcher.scan_once()
        self.assertEqual(self.registry.documents(), [])
        self.watcher.scan_once()
        self.assertEqual(len(self.registry.documents()), 1)
        self.assertEqual(len(self.registry.jobs()), 1)

    def test_stable_unchanged_files_are_hashed_and_observed_only_once(self):
        self.input.mkdir()
        invoice = self.input / "one.pdf"
        invoice.write_bytes(b"invoice")
        with mock.patch.object(self.registry, "hash_file",
                               wraps=self.registry.hash_file) as hash_file, \
                mock.patch.object(self.registry, "observe",
                                  wraps=self.registry.observe) as observe:
            for _ in range(6):
                self.watcher.scan_once()
        self.assertEqual(hash_file.call_count, 1)
        self.assertEqual(observe.call_count, 1)
        self.watcher.scan_once()
        self.assertEqual(len(self.registry.jobs()), 1)

    def test_modification_creates_version_and_deletion_marks_document(self):
        self.input.mkdir()
        invoice = self.input / "one.pdf"
        invoice.write_bytes(b"v1")
        self.watcher.scan_once()
        self.watcher.scan_once()
        original_id = self.registry.documents()[0]["document_id"]
        invoice.write_bytes(b"version two")
        self.watcher.scan_once()
        self.watcher.scan_once()
        document = self.registry.documents()[0]
        self.assertEqual(document["document_id"], original_id)
        self.assertEqual(document["version"], 2)
        self.assertEqual(len(self.registry.jobs()), 2)
        invoice.unlink()
        self.watcher.scan_once()
        self.assertEqual(self.registry.documents()[0]["status"], "deleted")
        self.assertEqual(self.registry.jobs()[-1]["action"], "delete")

    def test_move_preserves_id_when_content_is_unique(self):
        self.input.mkdir()
        first = self.input / "one.pdf"
        first.write_bytes(b"same invoice")
        self.watcher.scan_once()
        self.watcher.scan_once()
        original_id = self.registry.documents()[0]["document_id"]
        first.rename(self.input / "renamed.pdf")
        self.watcher.scan_once()
        self.watcher.scan_once()
        document = self.registry.documents()[0]
        self.assertEqual(document["document_id"], original_id)
        self.assertEqual(Path(document["path"]).name, "renamed.pdf")
        self.assertFalse(any(job["action"] == "delete" and job["status"] == "queued"
                             for job in self.registry.jobs()))

    def test_unsupported_and_oversized_files_are_ignored(self):
        self.input.mkdir()
        (self.input / "notes.txt").write_bytes(b"not an invoice")
        (self.input / "large.pdf").write_bytes(b"x" * 21)
        self.watcher.scan_once()
        self.watcher.scan_once()
        self.assertEqual(self.registry.documents(), [])

    def test_job_state_and_retry_are_persisted(self):
        self.input.mkdir()
        invoice = self.input / "one.pdf"
        invoice.write_bytes(b"invoice")
        self.watcher.scan_once()
        self.watcher.scan_once()
        job = self.registry.claim_next_job()
        self.assertEqual(job["status"], "processing")
        self.assertEqual(job["attempts"], 1)
        self.assertEqual(self.registry.fail_job(job["job_id"], "temporary issue", 2), "queued")
        retry = self.registry.claim_next_job()
        self.assertEqual(retry["attempts"], 2)
        self.assertEqual(self.registry.fail_job(retry["job_id"], "still failing", 2), "failed")
        self.assertEqual(self.registry.jobs()[0]["last_error"], "still failing")


if __name__ == "__main__":
    unittest.main()
