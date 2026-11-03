"""Safety and graph-content checks for the isolated presenter-data loader."""

import tempfile
import unittest
from pathlib import Path

from backend.app.demo import load_demo_records, reset_sqlite, seed_demo_graph
from backend.app.graph_rag import SQLiteGraphRetriever, summarize_graph_facts
from backend.app.neo4j_sync import derive_direct_invoice_relationships, derive_shared_item_hubs


class PresenterDemoTests(unittest.TestCase):
    def test_loads_ten_synthetic_records_and_seeds_reviewed_graph_links(self):
        with tempfile.TemporaryDirectory() as temp:
            graph = reset_sqlite(Path(temp) / "demo.sqlite3")
            records = load_demo_records()
            result = seed_demo_graph(graph, records)

            self.assertEqual(result, {"documents": 10, "approved": 9, "needs_review": 1})
            all_graph = graph.export()
            reviewed_graph = graph.export(reviewed_only=True)
            self.assertEqual(sum(node["type"] == "Invoice" for node in all_graph["nodes"]), 10)
            self.assertEqual(sum(node["type"] == "Invoice" for node in reviewed_graph["nodes"]), 9)
            review_states = {node["props"].get("invoice_number"): node["props"].get("review_status")
                             for node in all_graph["nodes"] if node["type"] == "Invoice"}
            self.assertEqual(review_states["DEMO-2026-010"], "needs_review")

            vendors = [node for node in reviewed_graph["nodes"] if node["type"] == "Vendor"]
            northstar = next(node for node in vendors
                             if "northstar office supply" in node["props"].get("match_key", ""))
            variants = northstar["props"].get("variants", [])
            self.assertGreaterEqual(len(variants), 2)

            hubs, hub_edges = derive_shared_item_hubs(reviewed_graph)
            links = derive_direct_invoice_relationships(reviewed_graph, hubs, hub_edges)
            self.assertTrue(any(link["type"] == "SAME_VENDOR_AS" for link in links))
            self.assertTrue(any(link["type"] == "SAME_CUSTOMER_AS" for link in links))
            self.assertTrue(any(link["type"] == "SAME_ITEM_DESCRIPTION_AS" for link in links))
            sequence_links = [link for link in links if link["type"] == "NEXT_INVOICE_NUMBER"]
            self.assertEqual(len(sequence_links), 8)
            self.assertTrue(all(link["props"]["meaning"].startswith("numbering order only")
                                for link in sequence_links))

            retrieved = SQLiteGraphRetriever(graph).retrieve(
                "Why is invoice DEMO-2026-001 connected?")
            self.assertTrue(retrieved["matches"])
            local_answer = summarize_graph_facts(retrieved)
            self.assertIn("DEMO-2026-001", local_answer)
            self.assertIn("DEMO-2026-003", local_answer)
            self.assertIn("same normalized item description", local_answer)

    def test_loader_rejects_non_synthetic_input(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text('{"document_id":"x","record":{}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "synthetic invoice record"):
                load_demo_records(temp)


if __name__ == "__main__":
    unittest.main()
