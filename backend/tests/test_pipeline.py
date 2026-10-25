"""Tests for providers, validation, RAG isolation, and graph invariants."""

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.extraction.providers import (FakeProvider, build_prompt,
                                              extract_validated, parse_model_json)
from backend.app.extraction.rag import TrainingRetriever, shape
from backend.app.graph import GraphStore

GOOD = {"invoice_number": "INV-1", "invoice_date": "01/02/2024",
        "customer": {"name": "Buyer LLC"}, "vendor": {"name": "Seller Inc", "tax_id": "S-9"},
        "items": [{"description": "Paper", "quantity": 2, "unit_price": 3.5, "amount": 7.0}],
        "subtotal": 7.0, "tax_amount": 0.7, "total_amount": 7.7}
SRC = {"source_file": "a.jpg"}


def gt(number, items=1):
    return {"gt_parse": {"header": {"invoice_no": number, "invoice_date": "03/12/2024"},
                         "items": [{"item_desc": f"x{i}"} for i in range(items)],
                         "summary": {"total_gross_worth": "10.00"}}}


class ProviderTests(unittest.TestCase):
    def test_valid_output_is_normalized_with_provenance(self):
        out = extract_validated(FakeProvider([GOOD]), None, SRC)
        self.assertEqual(out["record"]["invoice_number"], "INV-1")
        self.assertIsNone(out["record"]["customer"]["email"])
        self.assertEqual(out["record"]["review_status"], "needs_review")
        self.assertEqual(out["provenance"]["provider"], "fake")

    def test_fenced_json_string_is_parsed(self):
        out = extract_validated(FakeProvider(["Sure:\n```json\n" + json.dumps(GOOD) + "\n```"]), None, SRC)
        self.assertEqual(out["record"]["total_amount"], 7.7)

    def test_malformed_output_raises(self):
        for bad in ("no json here", "{broken", "[1,2]"):
            with self.assertRaises(ValueError):
                extract_validated(FakeProvider([bad]), None, SRC)

    def test_arithmetic_mismatch_flags_review_without_editing_values(self):
        bad = dict(GOOD, total_amount=99.0)
        out = extract_validated(FakeProvider([bad]), None, SRC)
        self.assertEqual(out["record"]["total_amount"], 99.0)
        self.assertEqual(out["record"]["review_status"], "needs_review")
        self.assertTrue(out["warnings"])

    def test_negative_or_garbage_numbers_become_null(self):
        out = extract_validated(FakeProvider([dict(GOOD, total_amount="abc", subtotal=-5)]), None, SRC)
        self.assertIsNone(out["record"]["total_amount"])
        self.assertIsNone(out["record"]["subtotal"])


class RagTests(unittest.TestCase):
    def _examples(self):
        return [{"row_index": i, "ground_truth": gt(f"{50000 + i}", items=i % 3 + 1)} for i in range(6)]

    def test_rejects_non_train_corpus(self):
        for split in ("validation", "test"):
            with self.assertRaises(ValueError):
                TrainingRetriever(self._examples(), split=split)

    def test_retrieval_returns_ids_and_shapes_never_values(self):
        r = TrainingRetriever(self._examples())
        ctx = r.retrieve("Invoice no: 51234 date 03/12/2024", k=2)
        self.assertEqual(len(ctx.hits), 2)
        self.assertTrue(all(h["id"].startswith("train:") for h in ctx.hits))
        self.assertIn("99999", ctx.guidance)
        self.assertNotIn("50001", ctx.guidance)
        self.assertEqual(shape("AB-12/x"), "AA-99/A")

    def test_retrieval_is_deterministic_and_ids_reach_provenance(self):
        r = TrainingRetriever(self._examples())
        a, b = r.retrieve("51234", 3), r.retrieve("51234", 3)
        self.assertEqual(a.hits, b.hits)
        provider = FakeProvider([GOOD])
        out = extract_validated(provider, None, SRC, a)
        self.assertEqual(out["provenance"]["retrieval_ids"], [h["id"] for h in a.hits])
        self.assertIs(provider.calls[0], a)
        self.assertIn("Never copy values", build_prompt(a))


class GraphTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.graph = GraphStore(Path(self._tmp.name) / "g.sqlite3")

    def rec(self, number="INV-1", vendor="Seller Inc"):
        return dict(GOOD, invoice_number=number, vendor={"name": vendor, "tax_id": "S-9"})

    def counts(self, kind=None):
        g = self.graph.export()
        return sum(1 for n in g["nodes"] if kind is None or n["type"] == kind)

    def test_resync_same_version_is_idempotent(self):
        self.assertTrue(self.graph.sync("d1", "h1", self.rec()))
        before = self.graph.export()
        self.assertFalse(self.graph.sync("d1", "h1", self.rec()))
        self.assertEqual(before, self.graph.export())

    def test_graph_has_typed_nodes_and_edges(self):
        self.graph.sync("d1", "h1", self.rec())
        g = self.graph.export()
        self.assertEqual({n["type"] for n in g["nodes"]},
                         {"Document", "Invoice", "Vendor", "Customer", "LineItem"})
        self.assertEqual({e["type"] for e in g["edges"]},
                         {"SUPPORTS", "ISSUED", "BILLED_TO", "CONTAINS"})

    def test_shared_vendor_survives_deleting_one_document(self):
        self.graph.sync("d1", "h1", self.rec("INV-1"))
        self.graph.sync("d2", "h2", self.rec("INV-2"))
        self.assertEqual(self.counts("Vendor"), 1)
        vendor = next(n for n in self.graph.export()["nodes"] if n["type"] == "Vendor")
        self.assertEqual(len(vendor["support"]), 2)
        self.graph.remove_document("d1")
        self.assertEqual(self.counts("Vendor"), 1)
        self.assertEqual(self.counts("Invoice"), 1)
        self.graph.remove_document("d2")
        self.assertEqual(self.counts(), 0)
        self.assertEqual(self.graph.export()["edges"], [])

    def test_changed_version_replaces_only_that_documents_facts(self):
        self.graph.sync("d1", "h1", self.rec("INV-1", "Old Vendor"))
        self.graph.sync("d2", "h2", self.rec("INV-2"))
        self.graph.sync("d1", "h1b", self.rec("INV-1", "New Vendor"))
        names = {n["props"]["name"] for n in self.graph.export()["nodes"] if n["type"] == "Vendor"}
        self.assertEqual(names, {"New Vendor", "Seller Inc"})
        self.assertEqual(self.counts("Invoice"), 2)

    def test_unknown_party_creates_no_node(self):
        self.graph.sync("d1", "h1", dict(self.rec(), customer={"name": None}))
        self.assertEqual(self.counts("Customer"), 0)


if __name__ == "__main__":
    unittest.main()


class ItemMetricTests(unittest.TestCase):
    def test_partial_item_credit_beats_zero_exact_list(self):
        from backend.app.extraction.evaluation import item_prf
        gold = [{"description": "A", "amount": 1.0}, {"description": "B", "amount": 2.0}]
        pred = [{"description": "a", "amount": 1.0}, {"description": "B", "amount": 9.0}]
        r = item_prf(pred, gold)
        self.assertEqual((r["matched"], r["precision"], r["recall"]), (1, 0.5, 0.5))
        self.assertEqual(item_prf([], gold)["recall"], 0.0)
