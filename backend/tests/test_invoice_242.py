"""Regression tests for invoice-242.jpg using a deterministic mocked provider."""

import tempfile
import unittest
from pathlib import Path

from backend.app.extraction.providers import FakeProvider, extract_validated
from backend.app.extraction.schema import normalize_donut_result
from backend.app.graph import GraphStore

FIXTURE = Path(__file__).parent / "fixtures" / "invoice-242.jpg"
INVOICE_242 = {
    "invoice_number": "95537278",
    "invoice_date": "11/10/2019",
    "vendor": {"name": "Hart, Rios and Williams",
               "address": "6637 Mack Junction Apt. 855\nEricport, AR 84395",
               "tax_id": "907-77-4589"},
    "customer": {"name": "Chavez-Wilcox",
                 "address": "7844 Carlson Expressway\nSouth Edwinborough, PA 19234",
                 "tax_id": "910-70-6111", "phone": None, "email": None},
    "bank_account_iban": "GB02TRCS19981598057841",
    "currency": None,
    "items": [
        {"description": "Day Trading Joe Ross New Hard\ncover English edition",
         "quantity": "4,00", "unit_price": "125,00", "net_amount": "500,00",
         "tax_rate_percent": "10%", "vat_amount": "50,00", "gross_amount": "550,00"},
        {"description": "Rage By Bob Woodward -\nDonald Trump NEW\nHARDCOVER Sept 2020",
         "quantity": "5,00", "unit_price": "22,93", "net_amount": "114,65",
         "tax_rate_percent": "10%", "vat_amount": "11,47", "gross_amount": "126,12"},
        {"description": "Florida's List of Protected Plants\nin Words and Pictures",
         "quantity": "2,00", "unit_price": "5,50", "net_amount": "11,00",
         "tax_rate_percent": "10%", "vat_amount": "1,10", "gross_amount": "12,10"},
        {"description": "The Art of Glen Loates", "quantity": "1,00",
         "unit_price": "6,08", "net_amount": "6,08", "tax_rate_percent": "10%",
         "vat_amount": "0,61", "gross_amount": "6,69"},
    ],
    "subtotal": "631,73", "tax_amount": "63,17", "total_amount": "694,90",
}


class Invoice242RegressionTests(unittest.TestCase):
    def test_complete_invoice_fields_and_order_are_preserved(self):
        self.assertTrue(FIXTURE.is_file(), "supplied invoice image fixture is missing")
        output = extract_validated(FakeProvider([INVOICE_242]), FIXTURE.read_bytes(),
                                   {"source_file": FIXTURE.name})
        record = output["record"]
        self.assertEqual(record["invoice_number"], "95537278")
        self.assertEqual(record["invoice_date"], "11/10/2019")
        self.assertEqual(record["vendor"]["name"], "Hart, Rios and Williams")
        self.assertEqual(record["vendor"]["address"],
                         "6637 Mack Junction Apt. 855\nEricport, AR 84395")
        self.assertEqual(record["customer"]["name"], "Chavez-Wilcox")
        self.assertEqual(record["customer"]["address"],
                         "7844 Carlson Expressway\nSouth Edwinborough, PA 19234")
        self.assertEqual(record["bank_account_iban"], "GB02TRCS19981598057841")
        self.assertIsNone(record["currency"])
        self.assertEqual(len(record["items"]), 4)
        self.assertEqual([item["source_row"] for item in record["items"]], [1, 2, 3, 4])
        self.assertEqual(record["items"][1]["description"],
                         "Rage By Bob Woodward -\nDonald Trump NEW\nHARDCOVER Sept 2020")
        self.assertEqual(record["items"][1]["unit_price"], 22.93)
        self.assertEqual(record["items"][1]["net_amount"], 114.65)
        self.assertEqual(record["items"][1]["vat_amount"], 11.47)
        self.assertEqual(record["items"][1]["gross_amount"], 126.12)
        self.assertAlmostEqual(record["subtotal"], 631.73)
        self.assertAlmostEqual(record["tax_amount"], 63.17)
        self.assertAlmostEqual(record["total_amount"], 694.90)
        self.assertEqual(output["validation_status"], "valid_reconciled")
        self.assertEqual(output["warnings"], [])
        self.assertIsNone(record["confidence"])
        self.assertIsNone(record["evidence"])
        self.assertTrue(any("confidence" in reason for reason in record["review_reasons"]))

    def test_missing_row_is_not_fabricated_and_summary_mismatch_is_reported(self):
        partial = dict(INVOICE_242, items=INVOICE_242["items"][:3])
        output = extract_validated(FakeProvider([partial]), FIXTURE.read_bytes(), {})
        self.assertEqual(len(output["record"]["items"]), 3)
        self.assertTrue(any("sum" in warning for warning in output["warnings"]))
        self.assertEqual(output["validation_status"], "arithmetic_mismatch")

    def test_wrong_quantity_is_flagged_without_changing_printed_values(self):
        incorrect = dict(INVOICE_242, items=[dict(item) for item in INVOICE_242["items"]])
        incorrect["items"][1]["quantity"] = 6
        output = extract_validated(FakeProvider([incorrect]), FIXTURE.read_bytes(), {})
        self.assertEqual(output["record"]["items"][1]["quantity"], 6)
        self.assertTrue(any("quantity" in warning or "net amount" in warning
                            for warning in output["warnings"]))

    def test_donut_label_mapping_preserves_old_amount_and_marks_its_meaning(self):
        result = normalize_donut_result({"gt_parse": {"items": [
            {"item_desc": "Book", "item_qty": "2", "item_net_price": "3,00",
             "item_net_worth": "6,00", "item_vat": "10%", "item_gross_worth": "6,60"}
        ]}}).to_dict()["items"][0]
        # Line VAT is not printed: it stays null. gross - net is kept apart for checks only.
        self.assertEqual((result["net_amount"], result["vat_amount"], result["gross_amount"]),
                         (6.0, None, 6.6))
        self.assertAlmostEqual(result["derived"]["vat_amount"]["value"], 0.6)
        self.assertEqual(result["derived"]["vat_amount"]["use"], "validation_only")
        self.assertEqual(result["field_origins"]["vat_amount"], "not_printed")
        self.assertEqual(result["amount"], 6.6)  # legacy field keeps its prior gross-first rule
        self.assertEqual(result["amount_semantics"], "gross")

    def test_legacy_graph_record_is_read_without_reinterpreting_amount(self):
        with tempfile.TemporaryDirectory() as temp:
            graph = GraphStore(Path(temp) / "graph.sqlite3")
            graph.sync("doc", "hash", {"invoice_number": "1", "items": [
                {"description": "Old item", "amount": 22.93}], "vendor": {}, "customer": {}})
            item = graph.get_extraction("doc")["record"]["items"][0]
            self.assertEqual(item["amount"], 22.93)
            self.assertEqual(item["amount_semantics"], "unknown")
            self.assertIsNone(item["net_amount"])
            self.assertIsNone(item["gross_amount"])

    def test_reviewed_extraction_requires_explicit_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            graph = GraphStore(Path(temp) / "graph.sqlite3")
            graph.sync("doc", "hash-1", {"invoice_number": "1", "items": [],
                       "vendor": {}, "customer": {}})
            graph.mark_reviewed("doc", "approved")
            with self.assertRaises(RuntimeError):
                graph.sync("doc", "hash-2", {"invoice_number": "2", "items": [],
                           "vendor": {}, "customer": {}})
            self.assertTrue(graph.sync("doc", "hash-2", {"invoice_number": "2", "items": [],
                               "vendor": {}, "customer": {}}, allow_reviewed_replace=True))


if __name__ == "__main__":
    unittest.main()
