"""Milestone 1: source ledger, review history and the deterministic graph.

These tests use replayed provider fixtures only (no model, no network).
"""

import json
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from backend.app.extraction.providers import FakeProvider, extract_validated
from backend.app.graph import GraphStore, ReviewedExtractionConflict
from backend.app.graph_ontology import EDGE_TYPES, OntologyError, build_claims, check_edge
from backend.app.ingestion.registry import DocumentRegistry
from backend.app.ingestion.watcher import FolderWatcher
from backend.app.pipeline import JobProcessor
from test_invoice_242 import INVOICE_242

SELLER = ("Seller Inc", "S-9")


def make(number="INV-1", vendor=SELLER, customer=("Buyer LLC", None), items=1, **extra):
    output = {
        "invoice_number": number, "invoice_date": "01/02/2024",
        "vendor": {"name": vendor[0], "address": "1 Main St", "tax_id": vendor[1]},
        "customer": ({"name": customer[0], "address": "2 High St", "tax_id": customer[1]}
                     if customer else {}),
        "items": [{"description": f"Item {i}", "quantity": "2,00", "unit_price": "3,50",
                   "net_amount": "7,00", "tax_rate_percent": "10%", "vat_amount": "0,70",
                   "gross_amount": "7,70"} for i in range(items)],
        "subtotal": f"{7 * items},00", "tax_amount": f"{0.7 * items:.2f}".replace(".", ","),
        "total_amount": f"{7.7 * items:.2f}".replace(".", ","),
    }
    output.update(extra)
    return output


def validated(output, source_file="a.png"):
    return extract_validated(FakeProvider([output]), None, {"source_file": source_file})["record"]


def dump(path):
    """Every row of every table, for all-or-nothing transaction checks."""
    db = sqlite3.connect(path)
    try:
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {name: sorted(map(repr, db.execute(f"SELECT * FROM {name}"))) for name in names}
    finally:
        db.close()


def ids(export, kind=None):
    return {item["id"] for item in export[kind]} if kind else (
        {item["id"] for item in export["nodes"]} | {item["id"] for item in export["edges"]})


class GraphTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "graph.sqlite3"
        self.graph = GraphStore(self.path)

    def nodes(self, node_type, export=None):
        export = export or self.graph.export()
        return [node for node in export["nodes"] if node["type"] == node_type]


class OntologyTests(unittest.TestCase):
    def test_edge_types_have_fixed_direction(self):
        check_edge("ISSUED", "Vendor:x", "Invoice:d1")
        with self.assertRaises(OntologyError):
            check_edge("ISSUED", "Invoice:d1", "Vendor:x")
        with self.assertRaises(OntologyError):
            check_edge("PAID_BY", "Invoice:d1", "Customer:x")

    def test_builder_emits_only_allowed_types_in_stable_order(self):
        record = validated(INVOICE_242)
        claims = build_claims("d1", "h1", 1, record, "invoice-242.jpg")
        self.assertEqual(claims, build_claims("d1", "h1", 1, record, "invoice-242.jpg"))
        for claim in claims:
            if claim["kind"] == "edge":
                self.assertEqual(EDGE_TYPES[claim["type"]][0], claim["src"].split(":")[0])
        self.assertNotIn("CLASSIFIED_AS", {claim["type"] for claim in claims})


class EntityAndSupportTests(GraphTestCase):
    def test_graph_from_four_item_invoice_keeps_parties_separate(self):
        self.graph.sync("d1", "h1", validated(INVOICE_242), filename="invoice-242.jpg", version=1)
        export = self.graph.export()
        vendor, customer = self.nodes("Vendor", export)[0], self.nodes("Customer", export)[0]
        self.assertEqual(vendor["props"]["name"], "Hart, Rios and Williams")
        self.assertEqual(customer["props"]["address"],
                         "7844 Carlson Expressway\nSouth Edwinborough, PA 19234")
        self.assertNotEqual(vendor["props"]["tax_id"], customer["props"]["tax_id"])
        items = sorted(self.nodes("LineItem", export), key=lambda n: n["props"]["row_index"])
        self.assertEqual(len(items), 4)
        self.assertEqual(items[1]["props"]["description"],
                         "Rage By Bob Woodward -\nDonald Trump NEW\nHARDCOVER Sept 2020")
        contains = [edge for edge in export["edges"] if edge["type"] == "CONTAINS"]
        self.assertEqual(sorted(edge["props"]["row_index"] for edge in contains), [0, 1, 2, 3])
        supports = next(edge for edge in export["edges"] if edge["type"] == "SUPPORTS")
        self.assertEqual(supports["props"]["review_status"], "needs_review")
        self.assertEqual(supports["support"][0]["version"], 1)

    def test_exact_match_keys_merge_spelling_variants_and_keep_display_values(self):
        self.graph.sync("d1", "h1", validated(make("A", vendor=("Seller Inc", "S-9"))))
        self.graph.sync("d2", "h2", validated(make("B", vendor=("SELLER   INC", "s 9"))))
        vendors = self.nodes("Vendor")
        self.assertEqual(len(vendors), 1)
        self.assertEqual(vendors[0]["props"]["name"], "Seller Inc")  # lowest document id
        self.assertEqual(sorted(v["name"] for v in vendors[0]["props"]["variants"]),
                         ["SELLER   INC", "Seller Inc"])
        self.assertEqual({s["document_id"] for s in vendors[0]["support"]}, {"d1", "d2"})

    def test_conflicting_identity_is_suggested_never_merged(self):
        self.graph.sync("d1", "h1", validated(make("A", vendor=("Seller Inc", "S-9"))))
        self.graph.sync("d2", "h2", validated(make("B", vendor=("Seller Inc", None))))
        self.graph.sync("d3", "h3", validated(make("C", vendor=("Seller Incorporated", "S-9"))))
        self.assertEqual(len(self.nodes("Vendor")), 3)
        reasons = {item["reason"] for item in self.graph.entity_suggestions()}
        self.assertEqual(reasons, {"same_name_different_tax_id", "same_tax_id_different_name"})

    def test_shared_vendor_survives_source_deletion_with_per_source_support(self):
        self.graph.sync("d1", "h1", validated(make("A")))
        self.graph.sync("d2", "h2", validated(make("B")))
        self.assertTrue(self.graph.remove_document("d1"))
        vendor = self.nodes("Vendor")[0]
        self.assertEqual([s["document_id"] for s in vendor["support"]], ["d2"])
        self.assertEqual([n["props"]["invoice_number"] for n in self.nodes("Invoice")], ["B"])
        self.assertFalse(self.graph.remove_document("d1"))  # already removed: no-op
        history = self.graph.extraction_history("d1")
        self.assertEqual([item["state"] for item in history], ["removed"])
        self.assertIsNone(self.graph.get_extraction("d1"))

    def test_non_invoice_document_writes_only_a_document_node(self):
        self.graph.sync("d1", "h1", validated(make(doc_type="receipt")))
        export = self.graph.export()
        self.assertEqual([node["type"] for node in export["nodes"]], ["Document"])
        self.assertEqual(export["nodes"][0]["props"]["doc_type"], "receipt")
        self.assertEqual(export["edges"], [])
        self.assertIn("document_type_unexpected",
                      [f["code"] for f in self.graph.get_extraction("d1")["record"]["findings"]])


class LedgerTests(GraphTestCase):
    def assert_last_change_matches(self, before, action):
        after = self.graph.export()
        change = self.graph.changes()[-1]
        self.assertEqual(change["action"], action)
        for kind in ("nodes", "edges"):
            old = {item["id"]: (item["type"], item["props"]) for item in before[kind]}
            new = {item["id"]: (item["type"], item["props"]) for item in after[kind]}
            self.assertEqual(change[f"{kind}_added"], sorted(set(new) - set(old)))
            self.assertEqual(change[f"{kind}_removed"], sorted(set(old) - set(new)))
            self.assertEqual(change[f"{kind}_updated"],
                             sorted(k for k in set(old) & set(new) if old[k] != new[k]))
        return change

    def test_create_update_review_delete_each_write_one_matching_ledger_row(self):
        before = self.graph.export()
        self.graph.sync("d1", "h1", validated(make("A", items=2)), version=1, run_id="job:1")
        change = self.assert_last_change_matches(before, "create")
        self.assertEqual((change["run_id"], change["version"], change["provider"]),
                         ("job:1", 1, "fake"))
        self.assertTrue(change["prompt_version"].startswith("schema-guidance-"))
        before = self.graph.export()
        self.graph.sync("d1", "h2", validated(make("A", items=1)), version=2)
        change = self.assert_last_change_matches(before, "update")
        self.assertEqual(change["nodes_removed"], ["LineItem:d1:1"])
        before = self.graph.export()
        self.graph.apply_review("d1", "approved", reviewer="alice")
        self.assert_last_change_matches(before, "review")
        before = self.graph.export()
        self.graph.remove_document("d1")
        self.assert_last_change_matches(before, "delete")
        self.assertEqual(len(self.graph.changes("d1")), 4)

    def test_repeat_sync_of_same_version_changes_nothing(self):
        record = validated(make())
        self.assertTrue(self.graph.sync("d1", "h1", record))
        snapshot = dump(self.path)
        self.assertFalse(self.graph.sync("d1", "h1", record))
        self.assertEqual(dump(self.path), snapshot)

    def test_explicit_reprocess_with_identical_output_logs_unchanged_only(self):
        record = validated(make())
        self.graph.sync("d1", "h1", record)
        self.assertFalse(self.graph.sync("d1", "h1", record, allow_same_version_replace=True))
        self.assertEqual([c["action"] for c in self.graph.changes()], ["create", "unchanged"])
        self.assertEqual(len(self.graph.extraction_history("d1")), 1)

    def test_changed_file_supersedes_and_keeps_history(self):
        self.graph.sync("d1", "h1", validated(make("OLD")), version=1)
        self.graph.sync("d1", "h2", validated(make("NEW")), version=2)
        history = self.graph.extraction_history("d1")
        self.assertEqual([(h["state"], h["version"]) for h in history],
                         [("superseded", 1), ("active", 2)])
        self.assertEqual(history[0]["superseded_by"], history[1]["extraction_id"])
        self.assertEqual(history[0]["record"]["invoice_number"], "OLD")
        self.assertEqual(self.nodes("Invoice")[0]["props"]["invoice_number"], "NEW")

    def test_synced_documents_view_lists_hash_version_state_and_review(self):
        self.graph.sync("d1", "h1", validated(make("A")), filename="a.png", version=1)
        self.graph.sync("d2", "h2", validated(make("B")), filename="b.png", version=3)
        self.graph.apply_review("d2", "approved")
        self.graph.remove_document("d1")
        rows = {row["document_id"]: row for row in self.graph.synced_documents()}
        self.assertEqual((rows["d1"]["state"], rows["d1"]["content_hash"]), ("removed", "h1"))
        self.assertEqual((rows["d2"]["version"], rows["d2"]["review_status"],
                          rows["d2"]["source_file"]), (3, "approved", "b.png"))
        self.assertTrue(rows["d2"]["last_synced_at"])


class ReviewTests(GraphTestCase):
    def test_correction_is_stored_beside_the_machine_record(self):
        self.graph.sync("d1", "h1", validated(make("A", vendor=("Seler Inc", "S-9"))))
        result = self.graph.apply_review("d1", "corrected", make("A"), reviewer="alice")
        stored = self.graph.get_extraction("d1")
        self.assertEqual(stored["record"]["vendor"]["name"], "Seler Inc")  # machine kept
        self.assertEqual(stored["effective_record"]["vendor"]["name"], "Seller Inc")
        self.assertEqual(stored["review_status"], "corrected")
        evidence = stored["effective_record"]["field_evidence"]
        self.assertEqual(evidence["vendor.name"]["method"], "human")
        self.assertEqual(evidence["invoice_number"]["method"], "vision_model")
        self.assertEqual(self.nodes("Vendor")[0]["props"]["name"], "Seller Inc")
        self.assertEqual(self.nodes("Invoice")[0]["props"]["review_status"], "corrected")
        self.assertIn("Vendor:", " ".join(result["change"]["nodes_added"]))

    def test_second_review_supersedes_but_keeps_the_first(self):
        self.graph.sync("d1", "h1", validated(make()))
        self.graph.apply_review("d1", "approved", reviewer="alice")
        self.graph.apply_review("d1", "corrected", make(number="INV-1B"), reviewer="bob")
        reviews = self.graph.extraction_history("d1")[0]["reviews"]
        self.assertEqual([(r["decision"], r["state"], r["reviewer"]) for r in reviews],
                         [("approved", "superseded", "alice"), ("corrected", "active", "bob")])

    def test_approval_after_correction_keeps_the_corrected_values(self):
        self.graph.sync("d1", "h1", validated(make("A", vendor=("Seler Inc", "S-9"))))
        self.graph.apply_review("d1", "corrected", make("A"), reviewer="alice")
        self.graph.apply_review("d1", "approved", reviewer="bob")
        stored = self.graph.get_extraction("d1")
        self.assertEqual(stored["review_status"], "approved")
        self.assertEqual(stored["effective_record"]["vendor"]["name"], "Seller Inc")
        self.assertEqual(stored["record"]["vendor"]["name"], "Seler Inc")
        self.assertEqual(self.nodes("Vendor")[0]["props"]["name"], "Seller Inc")

    def test_invalid_reviews_change_nothing(self):
        self.graph.sync("d1", "h1", validated(make()))
        snapshot = dump(self.path)
        with self.assertRaises(ValueError):
            self.graph.apply_review("d1", "approved", corrected_record=make())
        with self.assertRaises(ValueError):
            self.graph.apply_review("d1", "corrected", {"items": "not a list"})
        with self.assertRaises(ValueError):
            self.graph.apply_review("d1", "rejected")
        with self.assertRaises(KeyError):
            self.graph.apply_review("missing", "approved")
        self.assertEqual(dump(self.path), snapshot)

    def test_superseded_reviewed_version_keeps_its_review_history(self):
        self.graph.sync("d1", "h1", validated(make("A")), version=1)
        self.graph.apply_review("d1", "approved", reviewer="alice")
        with self.assertRaises(ReviewedExtractionConflict):
            self.graph.sync("d1", "h2", validated(make("A2")), version=2)
        self.assertTrue(self.graph.sync("d1", "h2", validated(make("A2")), version=2,
                                        allow_reviewed_replace=True))
        history = self.graph.extraction_history("d1")
        self.assertEqual([(h["state"], h["review_status"]) for h in history],
                         [("superseded", "approved"), ("active", "needs_review")])
        self.assertEqual(history[0]["reviews"][0]["reviewer"], "alice")
        self.assertEqual(self.graph.export(reviewed_only=True), {"nodes": [], "edges": []})

    def test_reviewed_only_view_excludes_unreviewed_support_and_merges(self):
        self.graph.sync("d1", "h1", validated(make("A")))
        self.graph.sync("d2", "h2", validated(make("B")))
        self.graph.apply_review("d1", "approved")
        full, reviewed = self.graph.export(), self.graph.export(reviewed_only=True)
        self.assertEqual(len(self.nodes("Invoice", full)), 2)
        self.assertEqual([n["props"]["invoice_number"] for n in self.nodes("Invoice", reviewed)],
                         ["A"])
        vendor = self.nodes("Vendor", reviewed)[0]
        self.assertEqual([(s["document_id"], s["review_status"]) for s in vendor["support"]],
                         [("d1", "approved")])
        statuses = {s["document_id"]: s["review_status"]
                    for s in self.nodes("Vendor", full)[0]["support"]}
        self.assertEqual(statuses, {"d1": "approved", "d2": "needs_review"})


class TransactionTests(GraphTestCase):
    def test_failure_mid_write_leaves_graph_and_ledger_unchanged(self):
        self.graph.sync("d1", "h1", validated(make("A")))
        self.graph.sync("d2", "h2", validated(make("B")))
        snapshot = dump(self.path)
        operations = [
            lambda: self.graph.sync("d1", "h1b", validated(make("A2", items=3))),
            lambda: self.graph.remove_document("d2"),
            lambda: self.graph.apply_review("d1", "corrected", make("A3")),
            lambda: self.graph.sync("d3", "h3", validated(make("C"))),
        ]
        with mock.patch.object(GraphStore, "_materialize_refs",
                               side_effect=RuntimeError("disk full")):
            for operation in operations:
                with self.assertRaises(RuntimeError):
                    operation()
                self.assertEqual(dump(self.path), snapshot)

    def test_concurrent_writers_serialize_without_drift(self):
        import threading
        records = {f"d{i}": validated(make(f"INV-{i}", items=2)) for i in range(8)}
        errors = []

        def worker(document_id):
            try:
                store = GraphStore(self.path)
                store.sync(document_id, "h1", records[document_id])
                store.apply_review(document_id, "approved")
                if document_id in ("d0", "d1"):
                    store.remove_document(document_id)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(doc,)) for doc in records]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertFalse(any(self.graph.rebuild_graph().values()))
        self.assertEqual(len(self.nodes("Invoice")), 6)
        self.assertEqual(len(self.nodes("Vendor")[0]["support"]), 6)
        self.assertEqual(len(self.graph.changes()), 8 * 2 + 2)

    def test_databases_use_wal_journal(self):
        registry_path = Path(self._tmp.name) / "registry.sqlite3"
        DocumentRegistry(registry_path)
        for path in (self.path, registry_path):
            db = sqlite3.connect(path)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            db.close()


class UnionInvariantTests(GraphTestCase):
    VENDORS = [("Seller Inc", "S-9"), ("seller  INC", "S 9"), ("Other GmbH", "DE-1"),
               ("Third Ltd", None)]
    CUSTOMERS = [("Buyer LLC", None), ("Buyer LLC", "B-1"), None]

    def expected(self, model, reviewed_only=False):
        refs = set()
        for document_id, (content_hash, record, status) in model.items():
            if reviewed_only and status == "needs_review":
                continue
            refs |= {c["ref_id"] for c in build_claims(document_id, content_hash, None, record)}
        return refs

    def random_output(self, rng):
        return make(f"INV-{rng.randrange(100)}", vendor=rng.choice(self.VENDORS),
                    customer=rng.choice(self.CUSTOMERS), items=rng.randint(0, 3),
                    doc_type=rng.choice([None, None, None, "invoice", "other"]))

    def test_graph_equals_union_of_active_support_after_random_operations(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                graph = GraphStore(Path(self._tmp.name) / f"union-{seed}.sqlite3")
                rng, model = random.Random(seed), {}
                for _step in range(60):
                    doc = f"d{rng.randrange(5)}"
                    op = rng.choice(["sync", "sync", "sync", "delete", "review", "reprocess"])
                    if op in ("sync", "reprocess"):
                        content_hash = (model[doc][0] if op == "reprocess" and doc in model
                                        else f"h{rng.randrange(4)}")
                        output = self.random_output(rng)
                        try:
                            changed = graph.sync(doc, content_hash, validated(output),
                                                 allow_reviewed_replace=rng.random() < 0.5,
                                                 allow_same_version_replace=op == "reprocess")
                        except ReviewedExtractionConflict:
                            changed = False
                        if changed:
                            model[doc] = (content_hash, validated(output), "needs_review")
                    elif op == "delete":
                        graph.remove_document(doc)
                        model.pop(doc, None)
                    elif doc in model:
                        if rng.random() < 0.5:
                            graph.apply_review(doc, "approved")
                            model[doc] = (model[doc][0], model[doc][1], "approved")
                        else:
                            corrected = self.random_output(rng)
                            graph.apply_review(doc, "corrected", corrected)
                            model[doc] = (model[doc][0], validated(corrected), "corrected")
                    self.assertEqual(ids(graph.export()), self.expected(model))
                    self.assertEqual(ids(graph.export(reviewed_only=True)),
                                     self.expected(model, reviewed_only=True))
                    self.assertFalse(any(graph.rebuild_graph().values()))
                for document_id in model:
                    self.assertIsNotNone(graph.get_extraction(document_id))


class MigrationTests(unittest.TestCase):
    LEGACY_DDL = [
        "CREATE TABLE nodes (node_id TEXT PRIMARY KEY, type TEXT NOT NULL, props TEXT NOT NULL)",
        "CREATE TABLE edges (edge_id TEXT PRIMARY KEY, type TEXT NOT NULL, src TEXT NOT NULL, "
        "dst TEXT NOT NULL)",
        "CREATE TABLE support (document_id TEXT NOT NULL, content_hash TEXT NOT NULL, kind TEXT "
        "NOT NULL, ref_id TEXT NOT NULL, evidence TEXT, PRIMARY KEY (document_id, kind, ref_id))",
        "CREATE TABLE synced (document_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL)",
        "CREATE TABLE extractions (document_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, "
        "record TEXT NOT NULL, warnings TEXT NOT NULL)",
    ]

    def test_v1_database_is_upgraded_without_losing_records_or_reviews(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.sqlite3"
            db = sqlite3.connect(path)
            for statement in self.LEGACY_DDL:
                db.execute(statement)
            approved = {"invoice_number": "1", "review_status": "approved",
                        "vendor": {"name": "Seller Inc", "tax_id": "S-9"}, "customer": {},
                        "items": [], "provenance": {"version": 2, "source_file": "a.png"}}
            legacy_item = {"invoice_number": "2", "vendor": {"name": "Seller Inc", "tax_id": "S-9"},
                           "customer": {}, "items": [{"description": "Old", "amount": 22.93}]}
            db.execute("INSERT INTO extractions VALUES ('d1','h1',?, '[]')", (json.dumps(approved),))
            db.execute("INSERT INTO extractions VALUES ('d2','h2',?, '[\"w\"]')",
                       (json.dumps(legacy_item),))
            db.execute("INSERT INTO nodes VALUES ('Vendor:stale', 'Vendor', '{}')")
            db.commit()
            db.close()

            graph = GraphStore(path)
            tables = set(dump(path))
            self.assertTrue({"legacy_v1_extractions", "legacy_v1_support",
                             "legacy_v1_synced"} <= tables)
            first = graph.get_extraction("d1")
            self.assertEqual((first["review_status"], first["version"]), ("approved", 2))
            self.assertEqual(first["review"]["reviewer"], "legacy-import")
            item = graph.get_extraction("d2")["record"]["items"][0]
            self.assertEqual((item["amount"], item["amount_semantics"]), (22.93, "unknown"))
            self.assertEqual(graph.get_extraction("d2")["warnings"], ["w"])
            export = graph.export()
            self.assertNotIn("Vendor:stale", ids(export, "nodes"))
            vendor = [n for n in export["nodes"] if n["type"] == "Vendor"]
            self.assertEqual(len(vendor), 1)
            self.assertEqual(len(vendor[0]["support"]), 2)
            self.assertEqual([c["action"] for c in graph.changes()], ["migrate"])
            GraphStore(path)  # re-running the migration is a no-op
            self.assertEqual(len(graph.extraction_history("d1")), 1)
            self.assertEqual(len(graph.changes()), 1)


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.input = root / "in"
        self.input.mkdir()
        self.registry = DocumentRegistry(root / "registry.sqlite3")
        self.graph = GraphStore(root / "graph.sqlite3")
        self.watcher = FolderWatcher(self.input, self.registry, max_file_bytes=10_000)

    def run_jobs(self, outputs):
        self.watcher.scan_once()
        self.watcher.scan_once()
        processor = JobProcessor(self.registry, self.graph, FakeProvider(outputs),
                                 image_loader=lambda path: object())
        return processor.process_pending()

    def test_identical_bytes_at_two_paths_are_two_documents_flagged_as_duplicates(self):
        (self.input / "a.png").write_bytes(b"same bytes")
        (self.input / "copy-of-a.png").write_bytes(b"same bytes")
        results = self.run_jobs([INVOICE_242, INVOICE_242])
        self.assertEqual([r["outcome"] for r in results], ["synced", "synced"])
        documents = self.registry.documents()
        self.assertEqual(len({d["document_id"] for d in documents}), 2)
        export = self.graph.export()
        self.assertEqual(len([n for n in export["nodes"] if n["type"] == "Invoice"]), 2)
        vendor = [n for n in export["nodes"] if n["type"] == "Vendor"]
        self.assertEqual((len(vendor), len(vendor[0]["support"])), (1, 2))
        groups = self.graph.duplicate_content_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(d["source_file"] for d in groups[0]["documents"]),
                         ["a.png", "copy-of-a.png"])

    def test_repeated_provider_fixture_is_idempotent_and_ledgered_per_job(self):
        (self.input / "a.png").write_bytes(b"one")
        self.run_jobs([INVOICE_242])
        before = dump(Path(self._tmp.name) / "graph.sqlite3")
        self.assertEqual(self.run_jobs([INVOICE_242]), [])  # rescans queue nothing new
        self.assertEqual(dump(Path(self._tmp.name) / "graph.sqlite3"), before)
        change = self.graph.changes()[0]
        job = self.registry.jobs()[0]
        self.assertEqual((change["action"], change["run_id"], change["version"]),
                         ("create", f"job:{job['job_id']}", 1))

    def test_reviewed_document_change_fails_job_and_keeps_reviewed_graph(self):
        path = self.input / "a.png"
        path.write_bytes(b"v1")
        self.run_jobs([make("A")])
        document_id = self.registry.documents()[0]["document_id"]
        self.graph.apply_review(document_id, "approved")
        path.write_bytes(b"version two")
        results = self.run_jobs([make("B")])
        self.assertEqual(results[0]["outcome"], "failed")
        invoice = [n for n in self.graph.export()["nodes"] if n["type"] == "Invoice"][0]
        self.assertEqual((invoice["props"]["invoice_number"], invoice["props"]["review_status"]),
                         ("A", "approved"))


if __name__ == "__main__":
    unittest.main()
