import unittest
import tempfile
from types import SimpleNamespace

from backend.app.agent_evidence import build_evidence_cards, sentence_snippet
from kg_agent.models import Edge, Evidence, GraphSchema, Node
from kg_agent.runtime.agent import BusinessQAAgent
from kg_agent.runtime.answerer import AnswerGenerator, CitationFormatError
from kg_agent.runtime.context_builder import build_context
from kg_agent.runtime.query_transformer import QueryTransformer
from kg_agent.runtime.scoping import scope_evidence_to_question
from kg_agent.runtime.tools.graph import buyer_product_search, party_product_search


QUERY = "What's the payment status for Shaw, Williams and Johnston?"


def evidence(source_id, text, *, score=1.0, kind="chunk", chunk_ids=None):
    return Evidence(id=source_id, source_tool="keyword", kind=kind, text=text,
                    score=score, doc_id="demo", chunk_ids=chunk_ids or [source_id])


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class AgentAnswerTests(unittest.TestCase):
    def setUp(self):
        self.summary = evidence("summary-1",
            "ConversationSummary: Shaw account; summary_text: AP review is pending; "
            "synthetic: True", kind="entity")
        self.email = evidence("email-chunk-10817861",
            "sender_name: Finance Team; recipient_name: Shaw, Williams and Johnston; "
            "subject: Payment timing; sent_at: 2025-03-04; body: Could you share expected "
            "payment timing once AP review is complete?; email_id: 10817861", score=0.9)
        self.email_duplicate = self.email.model_copy(update={
            "id": "dense-chunk-copy", "source_tool": "dense", "score": 0.8,
            "chunk_ids": ["dense-copy"]})
        self.context = build_context([self.summary, self.email, self.email_duplicate])

    @staticmethod
    def sample_conversation_context():
        return build_context([
            Evidence(id="thread-summary", source_tool="graph", kind="entity", score=1.0,
                node_ids=["summary"], text=("SYNTHETIC DEMO THREAD SUMMARY — Invoice 10817861 "
                    "from Hughes-Jones to Shaw, Williams and Johnston is marked needs_review. "
                    "The 10-email thread ends awaiting verification.")),
            Evidence(id="email-payment-question", source_tool="dense", kind="chunk", score=0.9,
                node_ids=["email-1"], text=("SYNTHETIC DEMO EMAIL — sender_name: Hughes-Jones; "
                    "recipient_name: Shaw, Williams and Johnston; subject: Payment timing; "
                    "body: Please share the expected payment timing once AP review is complete.; "
                    "email_id: mail-1")),
            Evidence(id="email-customer-reply", source_tool="dense", kind="chunk", score=0.8,
                node_ids=["email-2"], text=("SYNTHETIC DEMO EMAIL — sender_name: Shaw, Williams and Johnston; "
                    "recipient_name: Hughes-Jones; subject: Payment plan; "
                    "body: Payment is planned after approval, but has not been sent or received.; "
                    "email_id: mail-2")),
        ])

    def test_sample_briefing_separates_plan_from_completed_payment(self):
        context = self.sample_conversation_context()
        llm = FakeLLM([
            "Payment is not confirmed. Invoice 10817861 from Hughes-Jones is still marked "
            "needs_review [N1]. In the 10-email thread, Hughes-Jones asks for expected payment "
            "timing after AP review [N2]. The customer says payment is planned after approval, "
            "but that does not confirm it was sent or received [N3].\n"
            "Confirmed: the invoice exists, needs review, and the customer stated a payment plan.\n"
            "Not confirmed: payment sent or received, invoice approval, or AP completion.\n"
            "Sources: N1 (thread summary), N2 (vendor email to customer), "
            "N3 (customer reply to vendor)."
        ])
        answer = AnswerGenerator(llm).answer(
            "What's the current payment status for Shaw, Williams and Johnston?", context)
        self.assertIn("payment is planned after approval", answer.answer.lower())
        self.assertIn("not confirmed: payment sent or received", answer.answer.lower())
        self.assertNotIn("payment was sent", answer.answer.lower())
        self.assertNotIn("synthetic/demo data", answer.answer.lower())
        self.assertEqual(answer.citations, ["N1", "N2", "N3"])

    def test_sample_followup_resolves_history_and_answers_only_new_question(self):
        context = self.sample_conversation_context()
        history = [
            {"role": "user", "content": "What is the payment status for Shaw, Williams and Johnston?"},
            {"role": "assistant", "content": "Payment isn't confirmed. The vendor asked about timing [N2]."},
        ]
        transformer = QueryTransformer(SimpleNamespace(glossary={}, schema=lambda: None,
            all_nodes=lambda: []))
        standalone = transformer._standalone("Who asked about the payment timing first?", history)
        self.assertIn("Follow-up question: Who asked about the payment timing first?", standalone)
        llm = FakeLLM([
            "Hughes-Jones asked first, requesting the expected payment timing after AP review [N2]. "
            "The customer's reply came later [N3]. The records don't give the exact send dates, "
            "so I can't say how many days apart the messages were.\n"
            "Sources: N2 (email from Hughes-Jones), N3 (customer reply)."
        ])
        answer = AnswerGenerator(llm).answer(standalone, context)
        self.assertIn("Hughes-Jones asked first", answer.answer)
        self.assertNotIn("payment is not confirmed", answer.answer.lower())
        self.assertEqual(answer.citations, ["N2", "N3"])

    def test_sample_unavailable_amount_and_due_date_stays_unknown(self):
        context = self.sample_conversation_context()
        llm = FakeLLM([
            "The retrieved records don't include an invoice amount or due date. They cover the "
            "review status and payment-timing discussion, so I can't verify those fields here [N1].\n"
            "Sources: N1 (thread summary)."
        ])
        answer = AnswerGenerator(llm).answer("What's the invoice amount and due date?", context)
        self.assertIn("don't include an invoice amount or due date", answer.answer)
        self.assertNotIn("$", answer.answer)
        self.assertEqual(answer.citations, ["N1"])

    def test_sample_pushback_does_not_turn_plan_into_paid_or_approved(self):
        context = self.sample_conversation_context()
        llm = FakeLLM([
            "No, the records don't support that. Invoice 10817861 is still marked needs_review [N1], "
            "and the customer said payment was planned after approval, not that it happened [N3]. "
            "Nothing here records invoice approval, AP completion, or payment sent or received.\n"
            "Sources: N1 (thread summary), N3 (customer reply)."
        ])
        answer = AnswerGenerator(llm).answer("So it's basically approved and paid, right?", context)
        self.assertIn("don't support that", answer.answer)
        self.assertIn("not that it happened", answer.answer)
        self.assertNotIn("invoice is approved", answer.answer.lower())
        self.assertEqual(answer.citations, ["N1", "N3"])

    def test_duplicate_email_is_one_source_and_one_citation(self):
        llm = FakeLLM([
            "Payment is not confirmed. The email asks for expected payment timing after AP review. "
            "[N2] The review outcome isn't in these records. [N1] "
            "\nNot confirmed: payment, approval, or AP completion. "
            "\nSources: N2 (email to Shaw, Williams and Johnston), N1 (conversation summary)."
        ])
        answer = AnswerGenerator(llm).answer(QUERY, self.context)
        self.assertEqual(len(self.context.items), 2)
        self.assertEqual(self.context.items[1].citation_id, "N2")
        self.assertEqual(len(self.context.source_metadata["sources"]["N2"]["source_ids"]), 2)
        self.assertEqual(answer.citations, ["N2", "N1"])
        self.assertEqual(answer.citations.count("N2"), 1)
        prompt = llm.calls[0][-1]["content"]
        self.assertEqual(prompt.count("[N2]"), 1)
        self.assertNotIn("email_id", prompt)
        self.assertNotIn("body:", prompt.lower())

    def test_payment_status_regression_omits_synthetic_label(self):
        llm = FakeLLM([
            "Payment hasn't been confirmed. The email asks for expected payment timing once "
            "the AP review is complete, but no payment is recorded and the review outcome "
            "isn't in the available evidence. The summary notes the review is pending [N1]. "
            "The AP review result is the next thing to check. (Note: these records are labelled "
            "synthetic demo data.) [N2]\nNot confirmed: payment, approval, or AP completion. "
            "\nSources: N2 (email to Shaw, Williams and Johnston), "
            "N1 (conversation summary)."
        ])
        answer = AnswerGenerator(llm).answer(QUERY, self.context)
        self.assertIn("payment hasn't been confirmed", answer.answer.lower())
        self.assertIn("asks for expected payment timing", answer.answer.lower())
        self.assertIn("review outcome", answer.answer.lower())
        self.assertNotIn("synthetic/demo data", answer.answer.lower())
        self.assertNotIn("email_id", answer.answer)
        self.assertNotIn("ConversationSummary", answer.answer)
        self.assertNotIn("body:", answer.answer.lower())
        self.assertNotIn("...", answer.answer)
        self.assertEqual(answer.citations.count("N2"), 1)

    def test_formatter_failure_retries_with_simple_markers_and_sources(self):
        original = AnswerGenerator._citations
        calls = 0

        def fail_once(answer, context, *, require_sources):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise CitationFormatError("simulated citation formatter exception")
            return original(answer, context, require_sources=require_sources)

        llm = FakeLLM([
            "Payment is not confirmed. [N2]",
            "Payment is not confirmed. The email requested payment timing after AP review. "
            "[N2] The review result is not present. [N1]\nNot confirmed: payment, approval, "
            "or AP completion.\nSources: N2 (email to customer), "
            "N1 (conversation summary)."
        ])
        prior = AnswerGenerator._citations
        AnswerGenerator._citations = staticmethod(fail_once)
        try:
            answer = AnswerGenerator(llm).answer(QUERY, self.context)
        finally:
            AnswerGenerator._citations = staticmethod(prior)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("retry once", llm.calls[1][1]["content"].lower())
        self.assertEqual(answer.citations, ["N2", "N1"])
        self.assertIn("attempt 2", answer.reasoning_summary)

    def test_second_failure_returns_safe_prose_without_sources(self):
        llm = FakeLLM([RuntimeError("provider unavailable"),
                       "email_id:10817861 body: raw retrieved content..."])
        answer = AnswerGenerator(llm).answer(QUERY, self.context)
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(answer.citations, [])
        self.assertIn("Citations couldn't be attached", answer.answer)
        self.assertNotIn("email_id", answer.answer)
        self.assertNotIn("raw retrieved", answer.answer)
        self.assertNotIn("synthetic/demo data", answer.answer.lower())

    def test_sanitizer_failure_retries_before_returning_answer(self):
        llm = FakeLLM([
            "The message says email_id:10817861 and payment is pending. [N2] "
            "\nSources: N2 (email to customer).",
            "Payment is not confirmed; the email asks for expected payment timing after AP review. "
            "[N2] The summary notes that review is pending [N1], but its outcome isn't recorded. "
            "\nNot confirmed: payment, approval, or AP completion. "
            "\nSources: N2 (email to customer), N1 (conversation summary)."
        ])
        answer = AnswerGenerator(llm).answer(QUERY, self.context)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("payment is not confirmed", answer.answer.lower())
        self.assertNotIn("email_id", answer.answer)

    def test_ellipsis_sanitizer_retries(self):
        llm = FakeLLM([
            "Payment is still unclear... [N2]\nSources: N2 (email to customer).",
            "Payment is not confirmed. Expected payment timing was requested after AP review. [N2] "
            "\nNot confirmed: payment, approval, or AP completion. "
            "\nSources: N2 (email to customer)."
        ])
        answer = AnswerGenerator(llm).answer(QUERY, self.context)
        self.assertEqual(len(llm.calls), 2)
        self.assertNotIn("...", answer.answer)

    def test_validator_accepts_known_markers_and_rejects_unknown_marker(self):
        valid = "Payment hasn't been confirmed [N2].\nSources: N2 (email to customer)."
        self.assertEqual(AnswerGenerator._citations(valid, self.context, require_sources=True), ["N2"])
        invalid = "Payment hasn't been confirmed [N9].\nSources: N9 (email to customer)."
        with self.assertRaisesRegex(CitationFormatError, "unknown evidence markers"):
            AnswerGenerator._citations(invalid, self.context, require_sources=True)

    def test_canonical_graph_source_id_dedupes_repeated_invoice_evidence(self):
        first = Evidence(id="invoice-hit-a", source_tool="graph", kind="entity",
            text="Invoice 10817861 is marked needs_review.", node_ids=["invoice:10817861"])
        duplicate = first.model_copy(update={"id": "invoice-hit-b", "score": 0.5})
        context = build_context([first, duplicate])
        self.assertEqual(len(context.items), 1)
        self.assertEqual(context.items[0].citation_id, "N1")
        self.assertEqual(context.source_metadata["sources"]["N1"]["source_ids"],
                         ["invoice-hit-b", "invoice-hit-a"])

    def test_scope_filters_unrelated_customer_invoices(self):
        nodes = [
            Node(id="cust-shaw", type="Customer", name="Shaw, Williams and Johnston"),
            Node(id="invoice-10817861", type="Invoice", name="10817861",
                 properties={"invoice_number": "10817861", "review_status": "needs_review"}),
            Node(id="cust-other", type="Customer", name="Other Customer"),
            *[Node(id=f"invoice-{number}", type="Invoice", name=number,
                   properties={"invoice_number": number})
              for number in ("10060256", "10372826", "10299181", "10541959")],
            Node(id="summary-shaw", type="ConversationSummary", name="Shaw thread",
                 properties={"invoice_number": "10817861", "summary_text": "AP review pending."}),
            Node(id="email-timing", type="SyntheticEmail", name="Timing request",
                 properties={"email_id": "10817861", "invoice_number": "10817861",
                             "body": "Could you share expected payment timing once AP review is complete?"}),
        ]
        edges = [Edge(id="billed-shaw", source="cust-shaw", target="invoice-10817861", type="BILLED_TO"),
                 *[Edge(id=f"billed-{number}", source="cust-other", target=f"invoice-{number}",
                        type="BILLED_TO") for number in ("10060256", "10372826", "10299181", "10541959")]]
        store = SimpleNamespace(all_nodes=lambda: nodes, edges=edges)
        candidates = [Evidence(id="shaw-invoice", source_tool="graph", kind="entity",
                       text="Invoice 10817861 needs review.", node_ids=["invoice-10817861"]),
                     Evidence(id="shaw-email", source_tool="keyword", kind="chunk",
                       text="Could you share expected payment timing after AP review?", node_ids=["email-timing"])]
        candidates.extend(Evidence(id=f"other-{number}", source_tool="graph", kind="entity",
                          text=f"Invoice {number} unrelated", node_ids=[f"invoice-{number}"])
                          for number in ("10060256", "10372826", "10299181", "10541959"))
        scoped, resolved = scope_evidence_to_question(
            "What's the payment status for Shaw, Williams and Johnston?", store, candidates)
        self.assertEqual(resolved["name"], "Shaw, Williams and Johnston")
        self.assertEqual([item.id for item in scoped], ["shaw-invoice", "shaw-email"])

    def test_full_shaw_regression_scopes_dedupes_and_builds_compact_cards(self):
        query = ("What's the current payment status for Shaw, Williams and Johnston? "
                 "Summarize what the email thread says about payment timing and the AP review, "
                 "and clarify what the records do and don't confirm.")
        nodes = [
            Node(id="customer", type="Customer", name="Shaw, Williams and Johnston"),
            Node(id="invoice-10817861", type="Invoice", name="10817861",
                 properties={"invoice_number": "10817861", "review_status": "needs_review"}),
            Node(id="summary", type="ConversationSummary", name="Payment thread",
                 properties={"invoice_number": "10817861", "synthetic": True,
                             "summary_text": "AP review status is pending; the outcome is not recorded."}),
            Node(id="email-one", type="SyntheticEmail", name="Timing request",
                 properties={"email_id": "mail-1", "invoice_number": "10817861",
                    "sender_name": "Customer AP", "recipient_name": "Vendor AP",
                    "synthetic": True,
                    "body": "Could you share expected payment timing once AP review is complete?"}),
            Node(id="email-two", type="SyntheticEmail", name="Review follow-up",
                 properties={"email_id": "mail-2", "invoice_number": "10817861",
                    "sender_name": "Vendor AP", "recipient_name": "Customer AP",
                    "synthetic": True,
                    "body": "We have not received confirmation that the AP review is complete."}),
            *[Node(id=f"other-{number}", type="Invoice", name=number,
                   properties={"invoice_number": number})
              for number in ("10060256", "10372826", "10299181", "10541959")],
        ]
        edges = [Edge(id="billed", source="customer", target="invoice-10817861", type="BILLED_TO")]
        store = SimpleNamespace(all_nodes=lambda: nodes, edges=edges)
        candidates = [Evidence(id="summary-hit", source_tool="graph", kind="entity",
            text="ConversationSummary: summary_text: AP review status is pending; the outcome is not recorded. synthetic: True",
            score=1.0, node_ids=["summary"])]
        candidates.extend(Evidence(id=f"invoice-hit-{index}", source_tool="graph", kind="entity",
            text=f"Invoice 10817861 review_status: needs_review copy {index}", score=0.95-index*0.01,
            node_ids=["invoice-10817861"]) for index in range(5))
        candidates.extend([
            Evidence(id="email-chunk-1", source_tool="dense", kind="chunk",
                text="SYNTHETIC DEMO EMAIL — sender_name: Customer AP; recipient_name: Vendor AP; "
                     "body: Could you share expected payment timing once AP review is complete?; email_id: mail-1",
                score=0.9, node_ids=["email-one"]),
            Evidence(id="email-chunk-2", source_tool="dense", kind="chunk",
                text="SYNTHETIC DEMO EMAIL — sender_name: Vendor AP; recipient_name: Customer AP; "
                     "body: We have not received confirmation that the AP review is complete.; email_id: mail-2",
                score=0.8, node_ids=["email-two"]),
        ])
        candidates.extend(Evidence(id=f"unrelated-{number}", source_tool="graph", kind="entity",
            text=f"Invoice {number} unrelated payment details", node_ids=[f"other-{number}"])
            for number in ("10060256", "10372826", "10299181", "10541959"))
        scoped, _ = scope_evidence_to_question(query, store, candidates)
        context = build_context(scoped)
        self.assertEqual(len(context.items), 4)
        self.assertEqual(len(context.source_metadata["sources"]["N2"]["source_ids"]), 5)
        llm = FakeLLM([
            "Payment hasn't been confirmed. The first email asks for expected payment timing "
            "once AP review is complete [N3]. A follow-up says AP review completion hasn't "
            "been confirmed [N4]; the summary also says the review outcome is not recorded [N1]. "
            "\nNot confirmed: payment, approval, or AP completion. [N2]\n"
            "Note: these records are synthetic demo data.\n"
            "Sources: N1 (conversation summary), N2 (invoice 10817861 review record), "
            "N3 (customer email to vendor about payment timing), "
            "N4 (vendor email to customer about AP review)."
        ])
        answer = AnswerGenerator(llm).answer(query, context)
        self.assertEqual(len(answer.citations), 4)
        cited = [item.evidence.model_copy(update={"citation_id": item.citation_id})
                 for item in context.items if item.citation_id in answer.citations]
        cards = build_evidence_cards([item.model_dump() for item in cited],
            {node.id: {"id": node.id, "type": node.type, "props": node.properties}
             for node in nodes}, [])
        self.assertLessEqual(len(cards), 3)
        self.assertEqual(sum(len(card["supporting_emails"]) for card in cards), 2)
        card_citations = [citation for card in cards
            for citation in [*card["citation_ids"], *[
                citation for email in card["supporting_emails"]
                for citation in email["citation_ids"]]]]
        self.assertEqual(set(card_citations), set(answer.citations))
        self.assertEqual(len(card_citations), len(set(card_citations)))
        self.assertFalse(any(number in str(cards) for number in
            ("10060256", "10372826", "10299181", "10541959")))
        self.assertIn("Not confirmed: payment, approval, or AP completion", answer.answer)
        self.assertNotIn("synthetic/demo data", answer.answer.lower())
        self.assertNotIn("email_id", answer.answer)
        self.assertNotIn("body:", answer.answer.lower())
        self.assertNotIn("35%", str(cards))
        self.assertNotIn("email_id", str(cards))
        self.assertNotIn("...", str(cards))

    def test_snippet_strips_demo_label_and_truncation_at_sentence_boundary(self):
        snippet = sentence_snippet(
            "SYNTHETIC DEMO EMAIL — Could you share expected payment timing once AP review is complete? "
            "No payment is recorded... trailing fragment")
        self.assertEqual(snippet, "Could you share expected payment timing once AP review is complete?")

    def test_product_buyer_query_traverses_item_invoice_customer_with_citations(self):
        customer = Node(id="customer-1", type="Customer", name="Northwind Demo")
        invoice = Node(id="invoice-1", type="Invoice", name="PT-100",
            properties={"invoice_number": "PT-100", "review_status": "needs_review"})
        item = Node(id="item-1", type="LineItem", name="Silk rug",
            properties={"description": "Handmade Carpet Hand Knotted Silk Rug"})
        edges = [Edge(id="contains-item", source=invoice.id, target=item.id, type="CONTAINS"),
                 Edge(id="billed-to", source=customer.id, target=invoice.id, type="BILLED_TO")]
        nodes = [customer, invoice, item]
        store = SimpleNamespace(all_nodes=lambda: nodes, edges=edges,
            schema=lambda: GraphSchema(entity_types=[], relation_types=[], node_count=3,
                                       edge_count=2, chunk_count=0))
        question = "who all have bought the handmade silkrug carpet"
        self.assertIn("graph", QueryTransformer(store).transform(
            "who bought the handmade silk rug").tools)

        matches = buyer_product_search(store, question)
        self.assertEqual(len(matches), 1)
        self.assertEqual(set(matches[0].node_ids), {customer.id, invoice.id, item.id})
        self.assertEqual(set(matches[0].edge_ids), {"contains-item", "billed-to"})

        with tempfile.TemporaryDirectory() as runs_dir:
            response = BusinessQAAgent(store, runs_dir=runs_dir).run(question, mode="agent")
        self.assertIn("Northwind Demo", response.answer)
        self.assertIn("PT-100", response.answer)
        self.assertEqual(len(response.citations), 1)
        self.assertEqual(response.confidence, 0.9)

    def test_concatenated_sneaker_query_traverses_invoice_to_vendor(self):
        vendor = Node(id="vendor-1", type="Vendor", name="Carney-Rogers")
        invoice = Node(id="invoice-jordan", type="Invoice", name="20850516",
            properties={"invoice_number": "20850516", "review_status": "needs_review"})
        item = Node(id="jordan-item", type="LineItem", name="Jordan sneakers",
            properties={"description": "Jordan B'Loyal Boys Grade SchoolSneakersUS3Y"})
        edges = [Edge(id="jordan-contains", source=invoice.id, target=item.id, type="CONTAINS"),
                 Edge(id="vendor-issued", source=vendor.id, target=invoice.id, type="ISSUED")]
        nodes = [vendor, invoice, item]
        store = SimpleNamespace(all_nodes=lambda: nodes, edges=edges,
            schema=lambda: GraphSchema(entity_types=[], relation_types=[], node_count=3,
                                       edge_count=2, chunk_count=0))
        question = "which vendor sold jordansneakers"
        self.assertIn("graph", QueryTransformer(store).transform(question).tools)
        matches = party_product_search(store, question)
        self.assertEqual(len(matches), 1)
        self.assertEqual(set(matches[0].node_ids), {vendor.id, invoice.id, item.id})
        self.assertEqual(set(matches[0].edge_ids), {"jordan-contains", "vendor-issued"})
        with tempfile.TemporaryDirectory() as runs_dir:
            response = BusinessQAAgent(store, runs_dir=runs_dir).run(question, mode="agent")
        self.assertIn("Carney-Rogers", response.answer)
        self.assertIn("20850516", response.answer)
        self.assertIn("needs_review", response.answer)
        self.assertEqual(len(response.citations), 1)
        self.assertNotIn("synthetic/demo data", response.answer.lower())

    def test_new_catalog_item_can_be_searched_without_product_specific_rules(self):
        customer = Node(id="customer-fan", type="Customer", name="Fan Buyer")
        invoice = Node(id="invoice-fan", type="Invoice", name="FAN-100",
            properties={"invoice_number": "FAN-100", "review_status": "needs_review"})
        item = Node(id="fan-item", type="LineItem", name="Ultra Light Desk Fan",
            properties={"description": "Ultra Light Desk Fan with Remote"})
        edges = [Edge(id="fan-contains", source=invoice.id, target=item.id, type="CONTAINS"),
                 Edge(id="fan-billed-to", source=invoice.id, target=customer.id, type="BILLED_TO")]
        nodes = [customer, invoice, item]
        store = SimpleNamespace(all_nodes=lambda: nodes, edges=edges,
            schema=lambda: GraphSchema(entity_types=[], relation_types=[], node_count=3,
                                       edge_count=2, chunk_count=0))
        question = "who purchased ultralightdeskfan"
        self.assertIn("graph", QueryTransformer(store).transform(question).tools)
        matches = party_product_search(store, question)
        self.assertEqual(len(matches), 1)
        self.assertIn("Fan Buyer", matches[0].text)
        self.assertIn("FAN-100", matches[0].text)

    def test_card_titles_and_invoice_nested_email_are_canonical(self):
        nodes = {
            "invoice": {"id": "invoice", "type": "Invoice", "props": {
                "invoice_number": "10817861", "review_status": "needs_review"}},
            "email": {"id": "email", "type": "SyntheticEmail", "props": {
                "email_id": "10817861",
                "sender_name": "Accounts Payable", "recipient_name": "Shaw",
                "synthetic_notice": "SYNTHETIC DEMO EMAIL", "body":
                    "Could you share expected payment timing once AP review is complete?"}},
        }
        evidence_cards = build_evidence_cards([
            {"doc_id": "invoice", "node_ids": [], "citation_id": "N1", "text": "Invoice 10817861."},
            {"doc_id": "invoice", "node_ids": [], "citation_id": "N2", "text": "Invoice duplicate."},
            {"doc_id": "email", "node_ids": [], "citation_id": "N3", "text": "Email body."}],
            nodes, [{"source": "invoice", "target": "email", "type": "HAS_EMAIL"}])
        self.assertEqual(len(evidence_cards), 1)
        self.assertEqual(evidence_cards[0]["source"], "Invoice 10817861 (needs review)")
        self.assertEqual(evidence_cards[0]["citation_ids"], ["N1", "N2"])
        self.assertEqual(len(evidence_cards[0]["supporting_emails"]), 1)
        self.assertTrue(evidence_cards[0]["supporting_emails"][0]["synthetic"])


if __name__ == "__main__":
    unittest.main()
