"""Deterministic tests for the Neo4j projection and read-only Graph RAG path."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.graph_rag import (GRAPH_RAG_REMOTE_PHRASE, GraphAnswerProvider,
                                   Neo4jRetriever, explain_invoice_connections, query_terms,
                                   summarize_graph_facts)
from backend.app.graph import GraphStore
from backend.app.neo4j_sync import (derive_direct_invoice_relationships, derive_shared_item_hubs,
                                    export_neo4j_graph, prepare_snapshot, sync_graph_export)
from backend.app.review.server import ReviewService


def graph_export():
    return {
        "nodes": [
            {"id": "Document:d1", "type": "Document",
             "props": {"filename": "invoice-a.jpg"},
             "support": [{"document_id": "d1", "review_status": "approved"}]},
            {"id": "Invoice:d1", "type": "Invoice",
             "props": {"invoice_number": "INV-42", "review_status": "approved"},
             "support": [{"document_id": "d1", "review_status": "approved"}]},
            {"id": "Vendor:v1", "type": "Vendor",
             "props": {"name": "Acme Books", "variants": ["Acme Books", "ACME BOOKS"]},
             "support": [{"document_id": "d1", "review_status": "approved"}]},
            {"id": "LineItem:d1:0", "type": "LineItem",
             "props": {"description": "Invoice mapping book", "evidence": {"page": 1}},
             "support": [{"document_id": "d1", "review_status": "approved"}]},
        ],
        "edges": [
            {"id": "Document:d1-SUPPORTS->Invoice:d1", "type": "SUPPORTS",
             "source": "Document:d1", "target": "Invoice:d1", "props": {},
             "support": [{"review_status": "approved"}]},
            {"id": "Vendor:v1-ISSUED->Invoice:d1", "type": "ISSUED",
             "source": "Vendor:v1", "target": "Invoice:d1", "props": {},
             "support": [{"review_status": "approved"}]},
            {"id": "Invoice:d1-CONTAINS->LineItem:d1:0", "type": "CONTAINS",
             "source": "Invoice:d1", "target": "LineItem:d1:0", "props": {"row_index": 0},
             "support": [{"review_status": "approved"}]},
        ],
    }


class FakeResult:
    def consume(self):
        return None


class FakeTx:
    def __init__(self, calls):
        self.calls = calls

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return FakeResult()


class FakeSession:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return FakeResult()

    def execute_write(self, callback):
        return callback(FakeTx(self.calls))


class FakeNeo4jDriver:
    def __init__(self):
        self.calls = []

    def session(self, database=None):
        return FakeSession(self.calls)


class FakeRetrievalDriver:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def execute_query(self, query, **parameters):
        self.calls.append((query, parameters))
        return self.records, None, None


class FakeProjectionDriver:
    def execute_query(self, query, **parameters):
        if "RETURN properties(n)" in query:
            return ([{"properties": {"papertrail_id": "Invoice:d1", "papertrail_type": "Invoice",
                                      "invoice_number": "INV-42", "support_json":
                                      '[{"document_id":"d1","review_status":"approved"}]',
                                      "support_statuses": ["approved"], "_sync_generation": "g1"}}],
                    None, None)
        return ([{"source": "Vendor:v1", "target": "Invoice:d1", "type": "ISSUED",
                  "properties": {"papertrail_edge_id": "Vendor:v1-ISSUED->Invoice:d1",
                                 "_papertrail": True, "_sync_generation": "g1",
                                 "support_json": "[]"}}], None, None)


class Neo4jSyncTests(unittest.TestCase):
    def test_adding_synthetic_invoice_files_updates_only_supported_links(self):
        export = graph_export()
        self.assertEqual(derive_shared_item_hubs(export)[0], [])

        def add_invoice(document_id, invoice_number, vendor_id, vendor_name, description):
            support = [{"document_id": document_id, "review_status": "needs_review"}]
            if not any(node["id"] == vendor_id for node in export["nodes"]):
                export["nodes"].append({"id": vendor_id, "type": "Vendor",
                    "props": {"name": vendor_name}, "support": support})
            invoice_id = f"Invoice:{document_id}"
            line_id = f"LineItem:{document_id}:0"
            export["nodes"].extend([
                {"id": f"Document:{document_id}", "type": "Document",
                 "props": {"filename": f"{document_id}.jpg"}, "support": support},
                {"id": invoice_id, "type": "Invoice",
                 "props": {"invoice_number": invoice_number}, "support": support},
                {"id": line_id, "type": "LineItem",
                 "props": {"description": description}, "support": support},
            ])
            export["edges"].extend([
                {"id": f"Document:{document_id}-SUPPORTS->{invoice_id}", "type": "SUPPORTS",
                 "source": f"Document:{document_id}", "target": invoice_id, "props": {}, "support": support},
                {"id": f"{vendor_id}-ISSUED->{invoice_id}", "type": "ISSUED",
                 "source": vendor_id, "target": invoice_id, "props": {}, "support": support},
                {"id": f"{invoice_id}-CONTAINS->{line_id}", "type": "CONTAINS",
                 "source": invoice_id, "target": line_id, "props": {"row_index": 0}, "support": support},
            ])

        add_invoice("d2", "INV-43", "Vendor:v1", "Acme Books", "  INVOICE   MAPPING BOOK ")
        hubs, hub_edges = derive_shared_item_hubs(export)
        direct = derive_direct_invoice_relationships(export, hubs, hub_edges)
        self.assertEqual(len(hubs), 1)
        self.assertEqual({edge["type"] for edge in direct},
                         {"SAME_VENDOR_AS", "SAME_ITEM_DESCRIPTION_AS", "NEXT_INVOICE_NUMBER"})
        sequence = next(edge for edge in direct if edge["type"] == "NEXT_INVOICE_NUMBER")
        self.assertEqual((sequence["source"], sequence["target"]),
                         ("Invoice:d1", "Invoice:d2"))
        self.assertEqual(sequence["props"]["source_invoice_number"], "INV-42")
        self.assertEqual(sequence["props"]["target_invoice_number"], "INV-43")
        self.assertIn("numbering order only", sequence["props"]["meaning"])
        for edge in direct:
            if edge["type"] == "NEXT_INVOICE_NUMBER":
                continue
            self.assertEqual(len(edge["props"]["supporting_fact_ids"]), 2)
            self.assertTrue(all(fact_id in {item["id"] for item in export["edges"]}
                                or fact_id.startswith("LineItem:")
                                for fact_id in edge["props"]["supporting_fact_ids"]))
            self.assertTrue(edge["props"]["match_rule"])

        # A later file from a different vendor with a unique item adds no false links.
        add_invoice("d3", "INV-44", "Vendor:v2", "Northwind Paper", "New catalog item")
        hubs, hub_edges = derive_shared_item_hubs(export)
        direct = derive_direct_invoice_relationships(export, hubs, hub_edges)
        self.assertEqual(len(hubs), 1)
        self.assertEqual(sum(edge["type"] == "SAME_VENDOR_AS" for edge in direct), 1)
        self.assertEqual(sum(edge["type"] == "SAME_ITEM_DESCRIPTION_AS" for edge in direct), 1)
        self.assertEqual(sum(edge["type"] == "NEXT_INVOICE_NUMBER" for edge in direct), 2)
        _generation, _nodes, projected_edges = prepare_snapshot(export, generation="g-sequence")
        self.assertEqual(len(projected_edges["NEXT_INVOICE_NUMBER"]), 2)

    def test_invoice_sequence_links_skip_gaps_and_ambiguous_numbers(self):
        export = graph_export()
        export["nodes"][0:0] = [
            {"id": "Invoice:d2", "type": "Invoice", "props": {"invoice_number": "INV-44"}},
            {"id": "Invoice:d3", "type": "Invoice", "props": {"invoice_number": "INV-44"}},
            {"id": "Invoice:d4", "type": "Invoice", "props": {"invoice_number": "PO-43"}},
        ]
        links = [edge for edge in derive_direct_invoice_relationships(export, [], [])
                 if edge["type"] == "NEXT_INVOICE_NUMBER"]
        self.assertEqual(links, [])

    def test_local_summary_explains_sequence_as_ordering_only(self):
        answer = summarize_graph_facts({"matches": [{
            "node": {"papertrail_id": "Invoice:d1", "papertrail_type": "Invoice",
                     "invoice_number": "INV-42"},
            "links": [{"relationship": "NEXT_INVOICE_NUMBER",
                       "relationship_properties": {"source_invoice_number": "INV-42",
                                                   "target_invoice_number": "INV-43"},
                       "other_node": {"papertrail_id": "Invoice:d2",
                                      "papertrail_type": "Invoice",
                                      "invoice_number": "INV-43"}}]}]})
        self.assertIn("Invoice INV-42 is followed by Invoice INV-43", answer)
        self.assertIn("numbering order only", answer)

    def test_shared_descriptions_connect_invoices_through_one_hub(self):
        export = graph_export()
        support = [{"document_id": "d2", "review_status": "approved"}]
        export["nodes"].extend([
            {"id": "Document:d2", "type": "Document", "props": {"filename": "b.jpg"},
             "support": support},
            {"id": "Invoice:d2", "type": "Invoice", "props": {"invoice_number": "INV-43"},
             "support": support},
            {"id": "LineItem:d2:0", "type": "LineItem",
             "props": {"description": "  INVOICE   MAPPING BOOK "}, "support": support},
        ])
        export["edges"].extend([
            {"id": "Document:d2-SUPPORTS->Invoice:d2", "type": "SUPPORTS",
             "source": "Document:d2", "target": "Invoice:d2", "props": {}, "support": support},
            {"id": "Vendor:v1-ISSUED->Invoice:d2", "type": "ISSUED",
             "source": "Vendor:v1", "target": "Invoice:d2", "props": {}, "support": support},
            {"id": "Invoice:d2-CONTAINS->LineItem:d2:0", "type": "CONTAINS",
             "source": "Invoice:d2", "target": "LineItem:d2:0", "props": {"row_index": 0},
             "support": support},
        ])
        derived_nodes, derived_edges = derive_shared_item_hubs(export)
        self.assertEqual(len(derived_nodes), 1)
        self.assertEqual(derived_nodes[0]["props"]["invoice_count"], 2)
        self.assertEqual(len(derived_edges), 2)
        self.assertEqual({edge["type"] for edge in derived_edges}, {"MATCHES_DESCRIPTION"})
        generation, nodes, edges = prepare_snapshot(export, generation="g1")
        self.assertEqual(len(nodes["ItemDescription"]), 1)
        self.assertEqual(len(edges["MATCHES_DESCRIPTION"]), 2)
        self.assertEqual(len(edges["SAME_ITEM_DESCRIPTION_AS"]), 1)
        direct = edges["SAME_ITEM_DESCRIPTION_AS"][0]
        self.assertEqual(direct["properties"]["basis"], "item_description")
        self.assertEqual(direct["properties"]["shared_description"], "invoice mapping book")
        self.assertEqual(len(direct["properties"]["supporting_fact_ids"]), 2)
        compared = json.loads(direct["properties"]["compared_values_json"])
        self.assertEqual(set(compared), {"Invoice:d1", "Invoice:d2"})
        self.assertEqual(compared["Invoice:d1"], ["Invoice mapping book"])
        self.assertEqual(compared["Invoice:d2"], ["  INVOICE   MAPPING BOOK "])

    def test_snapshot_validates_directions_and_keeps_nested_data(self):
        generation, nodes, edges = prepare_snapshot(graph_export(), generation="g1")
        line_item = next(row for row in nodes["LineItem"])
        invoice = next(row for row in nodes["Invoice"])
        self.assertEqual(generation, "g1")
        self.assertEqual(invoice["properties"]["name"], "INV-42")
        self.assertEqual(json.loads(line_item["properties"]["evidence_json"]), {"page": 1})
        self.assertEqual(line_item["properties"]["support_statuses"], ["approved"])
        self.assertEqual(edges["ISSUED"][0]["source"], "Vendor:v1")

    def test_snapshot_rejects_mismatched_ids_and_edges(self):
        invalid_node = graph_export()
        invalid_node["nodes"][0]["id"] = "Invoice:d1"
        with self.assertRaisesRegex(ValueError, "does not match its type"):
            prepare_snapshot(invalid_node)
        invalid_edge = graph_export()
        invalid_edge["edges"][0]["target"] = "Vendor:v1"
        with self.assertRaisesRegex(ValueError, "must connect"):
            prepare_snapshot(invalid_edge)

    def test_sync_uses_whitelisted_labels_relationships_and_reconciles_stale_facts(self):
        driver = FakeNeo4jDriver()
        result = sync_graph_export(graph_export(), driver, database="neo4j", generation="g1")
        queries = [query for query, _ in driver.calls]
        self.assertEqual((result["nodes"], result["edges"]), (4, 3))
        self.assertTrue(any("MERGE (n:PaperTrail:Invoice" in q for q in queries))
        self.assertTrue(any("MERGE (s)-[r:ISSUED" in q for q in queries))
        self.assertTrue(any("r._papertrail = true" in q and "DELETE r" in q for q in queries))
        self.assertTrue(any("NOT (n)--() DETACH DELETE n" in q for q in queries))

    def test_export_reads_neo4j_properties_back_into_the_ui_contract(self):
        export = export_neo4j_graph(FakeProjectionDriver())
        self.assertEqual(export["nodes"][0]["id"], "Invoice:d1")
        self.assertEqual(export["nodes"][0]["props"]["invoice_number"], "INV-42")
        self.assertEqual(export["nodes"][0]["support"][0]["review_status"], "approved")
        self.assertEqual(export["edges"][0]["type"], "ISSUED")


class GraphRagTests(unittest.TestCase):
    def test_why_connected_uses_reviewed_sqlite_facts_and_cites_both_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            graph = GraphStore(Path(temp) / "graph.sqlite3")
            for document_id, invoice_number, vendor_name in (
                    ("d1", "INV-101", "Acme Traders"),
                    ("d2", "INV-102", "ACME Traders")):
                graph.sync(document_id, f"hash-{document_id}", {
                    "doc_type": "invoice", "invoice_number": invoice_number,
                    "vendor": {"name": vendor_name, "tax_id": "TX-55"}, "items": []},
                    filename=f"{invoice_number}.jpg")
                graph.apply_review(document_id, "approved")

            result = explain_invoice_connections(
                graph, "Why are invoice INV-101 and invoice INV-102 connected?")
            self.assertTrue(result["reviewed_only"])
            self.assertEqual(len(result["links"]), 2)
            self.assertIn("same vendor", result["answer"])
            self.assertIn("numbering order only", result["answer"])
            self.assertIn("Acme Traders", result["answer"])
            self.assertIn("ACME Traders", result["answer"])
            self.assertIn("SAME_VENDOR_AS", result["links"][0]["relationship"])
            self.assertEqual(len(result["links"][0]["supporting_fact_ids"]), 2)
            self.assertEqual({source["invoice_number"] for source in result["sources"]},
                             {"INV-101", "INV-102"})
            base = graph.export(reviewed_only=True)
            hubs, hub_edges = derive_shared_item_hubs(base)
            link = derive_direct_invoice_relationships(base, hubs, hub_edges)[0]
            self.assertEqual(link["props"]["compared_values"], {
                "Invoice:d1": {"name": "Acme Traders", "tax_id": "TX-55"},
                "Invoice:d2": {"name": "ACME Traders", "tax_id": "TX-55"},
            })

    def test_pair_query_does_not_expand_when_one_requested_invoice_is_unreviewed(self):
        with tempfile.TemporaryDirectory() as temp:
            graph = GraphStore(Path(temp) / "graph.sqlite3")
            for document_id, invoice_number in (
                    ("d1", "INV-101"), ("d2", "INV-102"), ("d3", "INV-103")):
                graph.sync(document_id, f"hash-{document_id}", {
                    "doc_type": "invoice", "invoice_number": invoice_number,
                    "vendor": {"name": "Shared Vendor"}, "items": []},
                    filename=f"{invoice_number}.jpg")
                if document_id != "d2":
                    graph.apply_review(document_id, "approved")

            question = "Why are invoice INV-101 and invoice INV-102 connected?"
            reviewed = explain_invoice_connections(graph, question)
            self.assertEqual(reviewed["links"], [])
            self.assertEqual(reviewed["excluded_unreviewed"], 1)
            self.assertIn("Excluded 1 unreviewed invoice", reviewed["answer"])
            self.assertNotIn("INV-103", reviewed["answer"])

            inclusive = explain_invoice_connections(graph, question, include_unreviewed=True)
            self.assertEqual(len(inclusive["links"]), 2)
            self.assertEqual(inclusive["links"][0]["review_statuses"],
                             ["approved", "needs_review"])

    def test_answer_cache_does_not_cross_review_filter_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            graph = GraphStore(Path(temp) / "graph.sqlite3")
            for document_id, invoice_number in (("d1", "INV-101"), ("d2", "INV-102")):
                graph.sync(document_id, f"hash-{document_id}", {
                    "doc_type": "invoice", "invoice_number": invoice_number,
                    "vendor": {"name": "Shared Vendor"}, "items": []},
                    filename=f"{invoice_number}.jpg")
                if document_id == "d1":
                    graph.apply_review(document_id, "approved")

            generated = []

            class FakeAnswerProvider:
                def __init__(self, settings):
                    pass

                def answer(self, question, retrieved):
                    answer = f"answer-{len(generated) + 1}"
                    generated.append((answer, retrieved["reviewed_only"]))
                    return answer

            service = ReviewService(graph, answer_provider_factory=FakeAnswerProvider)
            with mock.patch.dict(os.environ, {"GRAPH_RAG_BACKEND": "ollama",
                                               "GRAPH_RAG_MODEL": "local-test-model"}):
                reviewed = service.ask_graph("List invoices for Shared Vendor", generate=True)
                inclusive = service.ask_graph("List invoices for Shared Vendor", generate=True,
                                              include_unreviewed=True)
                reviewed_again = service.ask_graph("List invoices for Shared Vendor", generate=True)

            self.assertEqual([entry[1] for entry in generated], [True, False])
            self.assertEqual(reviewed["answer"], "answer-1")
            self.assertEqual(inclusive["answer"], "answer-2")
            self.assertEqual(reviewed_again["answer"], "answer-1")
            self.assertEqual(reviewed_again["answer_mode"], "ollama_cached")

    def test_hourly_answer_limit_falls_back_to_local_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            graph = GraphStore(Path(temp) / "graph.sqlite3")
            graph.sync("d1", "hash-d1", {
                "doc_type": "invoice", "invoice_number": "INV-101",
                "vendor": {"name": "Shared Vendor"}, "items": []},
                filename="INV-101.jpg")
            graph.apply_review("d1", "approved")
            generated = []

            class FakeAnswerProvider:
                def __init__(self, settings):
                    pass

                def answer(self, question, retrieved):
                    generated.append(question)
                    return f"model-answer-{len(generated)}"

            with mock.patch.dict(os.environ, {
                    "GRAPH_RAG_BACKEND": "ollama",
                    "GRAPH_RAG_MODEL": "local-test-model"}):
                service = ReviewService(graph, answer_provider_factory=FakeAnswerProvider)
                answers = [service.ask_graph(
                    f"List invoices for Shared Vendor; variant {index}", generate=True)
                    for index in range(11)]

            self.assertEqual(len(generated), 10)
            self.assertEqual([answer["answer_mode"] for answer in answers[:10]],
                             ["ollama"] * 10)
            self.assertEqual(answers[10]["answer_mode"], "local_budget_fallback")
            self.assertEqual(answers[-1]["llm_calls_used"], 10)
            self.assertEqual(answers[-1]["llm_calls_remaining"], 0)
            self.assertIn("Invoice INV-101", answers[-1]["answer"])

    def test_local_summary_answers_from_invoice_and_shared_vendor_facts(self):
        answer = summarize_graph_facts({"matches": [{
            "node": {"papertrail_id": "Invoice:d1", "papertrail_type": "Invoice",
                     "invoice_number": "INV-42", "invoice_date": "2024-01-02",
                     "total_amount": 100, "currency": "USD"},
            "links": [{"relationship": "SAME_VENDOR_AS", "other_node": {
                "papertrail_id": "Invoice:d2", "papertrail_type": "Invoice",
                "invoice_number": "INV-43"}},
                {"relationship": "ISSUED", "other_node": {
                    "papertrail_id": "Vendor:v1", "papertrail_type": "Vendor",
                    "name": "Acme"}}]}]})
        self.assertIn("Invoice INV-42", answer)
        self.assertIn("shares a vendor with Invoice INV-43", answer)
        self.assertIn("issued by Vendor Acme", answer)

    def test_local_summary_handles_no_matches_without_model(self):
        self.assertIn("No matching graph facts", summarize_graph_facts({"matches": []}))

    def test_terms_drop_stop_words_and_reject_empty_questions(self):
        self.assertEqual(query_terms("Which invoices came from Acme Books?"),
                         ["invoices", "came", "acme", "books"])
        with self.assertRaises(ValueError):
            query_terms("   ")

    def test_retrieval_is_parameterized_bounded_and_returns_source_ids(self):
        source = {"papertrail_id": "Invoice:d1", "papertrail_type": "Invoice",
                  "invoice_number": "INV-42", "review_status": "approved"}
        document = {"papertrail_id": "Document:d1", "papertrail_type": "Document",
                    "filename": "invoice-a.jpg", "support_statuses": ["approved"]}
        link = {"source_id": "Document:d1", "target_id": "Invoice:d1",
                "relationship": "SUPPORTS", "other_node": document}
        driver = FakeRetrievalDriver([{"matched_node": source, "score": 2, "links": [link]}])
        result = Neo4jRetriever(driver).retrieve("invoice INV-42", limit=999)
        query, params = driver.calls[0]
        self.assertIn("$terms", query)
        self.assertIn("OPTIONAL MATCH (m)-[r2]-(x:PaperTrail)", query)
        self.assertEqual(params["limit"], 20)
        self.assertEqual(params["neighbor_limit"], 18)
        self.assertTrue(params["reviewed_only"])
        self.assertEqual([item["id"] for item in result["sources"]],
                         ["Document:d1", "Invoice:d1"])

    def test_reviewed_retrieval_removes_unreviewed_support_details(self):
        center = {"papertrail_id": "Vendor:v1", "papertrail_type": "Vendor",
                  "address": "Unreviewed address",
                  "support_json": json.dumps([{"review_status": "approved"},
                                               {"review_status": "needs_review"}]),
                  "variants": ["old spelling"], "_sync_generation": "g1"}
        driver = FakeRetrievalDriver([{"matched_node": center, "score": 1, "links": []}])
        result = Neo4jRetriever(driver).retrieve("vendor acme")
        self.assertEqual(result["matches"][0]["node"]["supports"],
                         [{"review_status": "approved"}])
        self.assertNotIn("variants", result["matches"][0]["node"])
        self.assertNotIn("address", result["matches"][0]["node"])

    def test_no_useful_terms_short_circuits_database(self):
        driver = FakeRetrievalDriver([])
        result = Neo4jRetriever(driver).retrieve("and the of")
        self.assertEqual(result["matches"], [])
        self.assertEqual(driver.calls, [])

    def test_remote_answer_requires_separate_explicit_consent(self):
        with mock.patch.dict(os.environ, {"PAPERTRAIL_ALLOW_GRAPH_RAG_REMOTE": "",
                                          "GRAPH_RAG_API_KEY": "key",
                                          "GRAPH_RAG_MODEL": "model"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "PAPERTRAIL_ALLOW_GRAPH_RAG_REMOTE"):
                GraphAnswerProvider(mock.Mock())

    def test_answer_sends_only_retrieved_context_and_returns_text(self):
        captured = {}

        def transport(url, body, headers, timeout):
            captured.update({"url": url, "body": json.loads(body), "headers": headers})
            return 200, json.dumps({"content": [{"type": "text", "text": "Invoice INV-42."}]}).encode()

        with mock.patch.dict(os.environ, {
                "PAPERTRAIL_ALLOW_GRAPH_RAG_REMOTE": GRAPH_RAG_REMOTE_PHRASE,
                "GRAPH_RAG_API_KEY": "test-key", "GRAPH_RAG_MODEL": "test-model",
                "GRAPH_RAG_API_BASE_URL": "https://example.test"}, clear=False):
            provider = GraphAnswerProvider(mock.Mock(vision_api_key="", vision_model="",
                                                     vision_api_base_url="https://api.anthropic.com"),
                                           transport=transport)
        answer = provider.answer("What is the invoice number?", {
            "matches": [{"node": {"papertrail_id": "Invoice:d1"}, "links": []}],
            "sources": [{"id": "Invoice:d1"}]})
        self.assertEqual(answer, "Invoice INV-42.")
        self.assertEqual(captured["url"], "https://example.test/v1/messages")
        self.assertIn("Invoice:d1", captured["body"]["messages"][0]["content"])
        self.assertEqual(captured["headers"]["x-api-key"], "test-key")

    def test_ollama_answer_uses_loopback_without_hosted_consent_or_api_key(self):
        captured = {}

        def transport(url, body, headers, timeout):
            captured.update({"url": url, "body": json.loads(body), "headers": headers})
            return 200, json.dumps({"message": {"content": "Invoice INV-42 is from Acme."}}).encode()

        with mock.patch.dict(os.environ, {
                "GRAPH_RAG_BACKEND": "ollama", "GRAPH_RAG_MODEL": "qwen3-vl:4b",
                "OLLAMA_HOST": "http://127.0.0.1:11434",
                "PAPERTRAIL_ALLOW_GRAPH_RAG_REMOTE": "", "GRAPH_RAG_API_KEY": "",
        }, clear=False):
            provider = GraphAnswerProvider(mock.Mock(vision_api_key="", vision_model="",
                                                     vision_api_base_url="https://api.anthropic.com"),
                                           transport=transport)
        answer = provider.answer("Which invoice is this?", {
            "matches": [{"node": {"papertrail_id": "Invoice:d1"}, "links": []}],
            "sources": [{"id": "Invoice:d1"}]})
        self.assertEqual(answer, "Invoice INV-42 is from Acme.")
        self.assertEqual(provider.answer_mode, "ollama")
        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(captured["body"]["model"], "qwen3-vl:4b")
        self.assertFalse(captured["body"]["stream"])
        self.assertEqual(captured["body"]["keep_alive"], "10m")
        self.assertNotIn("x-api-key", captured["headers"])

    def test_ollama_refuses_non_loopback_host(self):
        with mock.patch.dict(os.environ, {"GRAPH_RAG_BACKEND": "ollama",
                                          "GRAPH_RAG_MODEL": "qwen3-vl:4b",
                                          "OLLAMA_HOST": "http://example.test:11434"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "loopback"):
                GraphAnswerProvider(mock.Mock(vision_api_key="", vision_model=""))


if __name__ == "__main__":
    unittest.main()
