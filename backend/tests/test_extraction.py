"""Tests for safe invoice normalization and evaluation metrics."""

import unittest
import json
import tempfile
from pathlib import Path

from backend.app.extraction.dataset import load_local_split
from backend.app.extraction.evaluation import evaluate_examples, evaluate_predictions
from backend.app.extraction.evaluate import comparison_markdown, evenly_spaced_indices
from backend.app.extraction.schema import (normalize_donut_result, parse_amount,
                                           parse_percent, validate_invoice)


class InvoiceSchemaTests(unittest.TestCase):
    def test_nested_donut_labels_map_to_canonical_schema(self):
        annotation = {
            "gt_parse": {
                "header": {
                    "invoice_no": "INV-42",
                    "invoice_date": "03/12/2024",
                    "client": "Buyer LLC 10 Main St",
                    "client_tax_id": "C-44",
                    "seller": "Seller Inc 20 Oak Ave",
                    "seller_tax_id": "S-9",
                    "iban": "GB00TEST",
                },
                "items": [{"item_desc": "Paper", "item_qty": "2", "item_net_price": "3.50",
                           "item_vat": "10%"}],
                "summary": {"total_gross_worth": "$7.00", "total_net_worth": "6.30",
                            "total_vat": "0.70"},
            }
        }
        record = normalize_donut_result(annotation).to_dict()
        self.assertEqual(record["invoice_number"], "INV-42")
        self.assertEqual(record["customer"]["name"], "Buyer LLC 10 Main St")
        self.assertIsNone(record["customer"]["phone"])
        self.assertEqual(record["customer"]["tax_id"], "C-44")
        self.assertEqual(record["vendor"]["tax_id"], "S-9")
        self.assertEqual(record["bank_account_iban"], "GB00TEST")
        self.assertEqual(record["total_amount"], 7.0)
        self.assertEqual(record["subtotal"], 6.3)
        self.assertEqual(record["items"][0]["tax_rate_percent"], 10.0)
        self.assertEqual(record["items"][0]["quantity"], 2.0)
        metrics = evaluate_predictions([{
            "ground_truth": annotation,
            "prediction": record,
        }])
        self.assertEqual(metrics["fields"]["line_item_quantities"]["accuracy"], 1.0)
        self.assertEqual(metrics["fields"]["vendor_tax_id"]["accuracy"], 1.0)
        self.assertEqual(metrics["fields"]["line_item_tax_rate_percent"]["accuracy"], 1.0)
        validate_invoice(record)

    def test_legacy_flat_annotation_and_unknown_amount_are_safe(self):
        record = normalize_donut_result({
            "gt_parse": {"None": {"invoice_no": "8", "client": "Buyer", "seller": "Shop"}}
        }).to_dict()
        self.assertEqual(record["invoice_number"], "8")
        self.assertIsNone(record["total_amount"])
        self.assertIsNone(parse_amount("not a value"))
        self.assertEqual(parse_amount("1,234.50"), 1234.50)
        self.assertEqual(parse_amount("1234,50"), 1234.50)
        self.assertEqual(parse_percent("10%"), 10)

    def test_line_total_legacy_label_is_supported(self):
        record = normalize_donut_result({
            "gt_parse": {
                "items": [{"item_desc": "Bottle", "total_net_worth": "12,32",
                           "item_vat": "10%"}],
            }
        }).to_dict()
        self.assertEqual(record["items"][0]["amount"], 12.32)
        self.assertEqual(record["items"][0]["tax_rate_percent"], 10.0)

    def test_evaluation_ignores_fields_absent_from_dataset_labels(self):
        truth = {"gt_parse": {"header": {"invoice_no": "42", "client": "Buyer"}}}
        prediction = normalize_donut_result(truth).to_dict()
        metrics = evaluate_predictions([{"ground_truth": truth, "prediction": prediction}])
        self.assertEqual(metrics["fields"]["invoice_number"]["accuracy"], 1.0)
        self.assertIsNone(metrics["fields"]["total_amount"]["accuracy"])
        self.assertIn("customer.email", metrics["unscored_schema_fields"])

    def test_example_report_includes_source_values_and_field_matches(self):
        truth = {"gt_parse": {"header": {"invoice_no": "42"}}}
        prediction = normalize_donut_result(truth).to_dict()
        examples = evaluate_examples([{
            "ground_truth": truth,
            "prediction": prediction,
            "source": {"dataset": "example", "split": "test", "row_index": 3},
        }])
        self.assertEqual(examples[0]["source"]["row_index"], 3)
        self.assertEqual(examples[0]["extracted_data"]["invoice_number"], "42")
        self.assertEqual(examples[0]["fields"]["invoice_number"]["exact_match"], True)
        self.assertIsNone(examples[0]["fields"]["total_amount"]["exact_match"])

    def test_local_split_loader_pairs_files_and_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "test"
            split_dir.mkdir()
            (split_dir / "invoice-003.jpg").write_bytes(b"image fixture")
            (split_dir / "manifest.json").write_text(json.dumps([{
                "row_index": 3,
                "file": "invoice-003.jpg",
                "ground_truth": '{"gt_parse":{"header":{"invoice_no":"A-3"}}}',
            }]), encoding="utf-8")
            examples, resolved_dir = load_local_split(Path(temp_dir), "test")
            self.assertEqual(examples[0]["row_index"], 3)
            self.assertEqual(examples[0]["image_path"].name, "invoice-003.jpg")
            self.assertEqual(resolved_dir, split_dir.resolve())

    def test_comparison_report_puts_prediction_and_truth_side_by_side(self):
        report = {
            "split": "test",
            "metrics": {"documents_evaluated": 1, "micro_field_accuracy": 1.0,
                        "exact_document_accuracy": 1.0},
            "examples": [{
                "source": {"file": "invoice-003.jpg", "split": "test", "row_index": 3},
                "fields": {"invoice_number": {
                    "predicted": "A-3", "expected": "A-3", "exact_match": True,
                }},
            }],
        }
        output = comparison_markdown(report)
        self.assertIn("invoice-003.jpg", output)
        self.assertIn("| Extracted | Expected annotation |", output)
        self.assertIn("| `invoice_number` | \"A-3\" | \"A-3\" | Yes |", output)

    def test_evenly_spaced_evaluation_indices_are_stable(self):
        self.assertEqual(evenly_spaced_indices(26, 5), [0, 6, 12, 19, 25])
        self.assertEqual(evenly_spaced_indices(2, 5), [0, 1])


if __name__ == "__main__":
    unittest.main()
