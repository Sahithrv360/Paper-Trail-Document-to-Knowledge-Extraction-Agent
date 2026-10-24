"""Milestone 1: canonical data contract, field evidence and structured validation.

All provider output here is replayed from fixtures; no model or network is used.
"""

import copy
from decimal import Decimal
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.app.extraction.evidence import (ParseContext, build_field_evidence,
                                             money_precision, source_match, validate_geometry,
                                             value_in_quote)
from backend.app.extraction.providers import (PROMPT_VERSION, FakeProvider,
                                              InvalidModelResponse, extract_validated)
from backend.app.extraction.schema import normalize_donut_result, record_from_canonical
from backend.app.extraction.validation import (CODE_EFFECTS, arithmetic_findings,
                                               status_from_findings)
from test_invoice_242 import INVOICE_242

# Text a parser could have produced for invoice-242 (whitespace, wraps and casing vary).
PARSER_TEXT_242 = """Invoice no: 95537278   Date of issue: 11/10/2019
Seller:                         Client:
Hart, Rios and Williams         Chavez-Wilcox
6637 Mack Junction Apt. 855     7844 Carlson Expressway
Ericport, AR 84395              South Edwinborough, PA 19234
Tax Id: 907-77-4589             Tax Id: 910-70-6111
IBAN: GB02TRCS19981598057841
ITEMS
No. Description Qty UM Net price Net worth VAT [%] Gross worth
1. Day Trading Joe Ross New Hard-
cover English edition 4,00 each 125,00 500,00 10% 550,00
2. Rage By Bob Woodward - Donald Trump NEW HARDCOVER Sept 2020 5,00 each 22,93 114,65 10% 126,12
3. Florida's List of Protected Plants in Words and Pictures 2,00 each 5,50 11,00 10% 12,10
4. The Art of Glen Loates 1,00 each 6,08 6,08 10% 6,69
SUMMARY  VAT [%] Net worth VAT Gross worth
10% 631,73 63,17 694,90
Total $ 631,73 $ 63,17 $ 694,90
"""


def run(output, parse=None):
    return extract_validated(FakeProvider([output]), None, {"source_file": "invoice-242.jpg"},
                             parse=parse)


def codes(result):
    return [item["code"] for item in result["findings"]]


def fixture(**changes):
    value = copy.deepcopy(INVOICE_242)
    value.update(changes)
    return value


class CanonicalRecordTests(unittest.TestCase):
    def test_complete_four_item_invoice_keeps_raw_text_beside_values(self):
        result = run(fixture())
        record = result["record"]
        self.assertEqual(result["validation_status"], "valid_reconciled")
        self.assertEqual(record["review_status"], "needs_review")
        self.assertEqual(len(record["items"]), 4)
        evidence = record["field_evidence"]
        self.assertEqual(evidence["items[0].unit_price"]["raw_text"], "125,00")
        self.assertEqual(evidence["items[0].unit_price"]["value"], 125.0)
        self.assertEqual(evidence["total_amount"]["raw_text"], "694,90")
        self.assertEqual(evidence["items[0].tax_rate_percent"]["raw_text"], "10%")
        self.assertEqual(evidence["invoice_number"]["method"], "vision_model")
        # Without provider evidence the record says so instead of inventing links.
        self.assertIn("evidence_missing", codes(result))
        self.assertTrue(all(item["effect"] == "none" for item in result["findings"]))
        self.assertEqual(result["provenance"]["prompt_version"], PROMPT_VERSION)
        self.assertEqual(record["validation_details"]["money_precision"], 2)

    def test_multiline_description_is_preserved_exactly(self):
        record = run(fixture())["record"]
        expected = "Rage By Bob Woodward -\nDonald Trump NEW\nHARDCOVER Sept 2020"
        self.assertEqual(record["items"][1]["description"], expected)
        self.assertEqual(record["field_evidence"]["items[1].description"]["raw_text"], expected)

    def test_decimal_comma_and_grouping_parse_without_losing_raw_text(self):
        record = run(fixture(total_amount="1.694,90", subtotal="1,631.73"))["record"]
        self.assertEqual(record["total_amount"], 1694.90)
        self.assertEqual(record["subtotal"], 1631.73)
        self.assertEqual(record["field_evidence"]["total_amount"]["raw_text"], "1.694,90")
        self.assertEqual(record["field_evidence"]["subtotal"]["raw_text"], "1,631.73")

    def test_typed_number_has_no_fabricated_raw_text(self):
        record = run(fixture(total_amount=694.9))["record"]
        entry = record["field_evidence"]["total_amount"]
        self.assertIsNone(entry["raw_text"])
        self.assertEqual(entry["raw_type"], "float")

    def test_vendor_and_customer_stay_independent(self):
        record = run(fixture())["record"]
        self.assertEqual(record["vendor"], {"name": "Hart, Rios and Williams",
                                            "address": "6637 Mack Junction Apt. 855\nEricport, AR 84395",
                                            "tax_id": "907-77-4589"})
        self.assertEqual(record["customer"]["name"], "Chavez-Wilcox")
        self.assertEqual(record["customer"]["tax_id"], "910-70-6111")
        self.assertNotIn("GB02", record["vendor"]["address"] + record["customer"]["address"])
        self.assertEqual(record["field_evidence"]["vendor.tax_id"]["raw_text"], "907-77-4589")
        self.assertEqual(record["field_evidence"]["customer.tax_id"]["raw_text"], "910-70-6111")

    def test_tax_fields_are_read_independently(self):
        item = run(fixture())["record"]["items"][1]
        self.assertEqual((item["net_amount"], item["tax_rate_percent"], item["vat_amount"],
                          item["gross_amount"]), (114.65, 10.0, 11.47, 126.12))

    def test_unprinted_line_vat_is_never_filled_by_arithmetic(self):
        record = normalize_donut_result({"gt_parse": {"items": [
            {"item_desc": "Book", "item_qty": "2", "item_net_price": "3,00",
             "item_net_worth": "6,00", "item_vat": "10%", "item_gross_worth": "6,60"}]}}).to_dict()
        item = record["items"][0]
        self.assertIsNone(item["vat_amount"])
        self.assertEqual(item["derived"]["vat_amount"]["use"], "validation_only")

    def test_jurisdiction_extensions_are_kept_raw_and_unvalidated(self):
        record = record_from_canonical(dict(INVOICE_242, extensions={
            "gstin": "29ABCDE1234F1Z5", "hsn": "4901", "nested": {"x": 1}})).to_dict()
        self.assertEqual(record["extensions"], {"gstin": "29ABCDE1234F1Z5", "hsn": "4901"})


class MalformedOutputTests(unittest.TestCase):
    def test_null_and_malformed_provider_outputs_are_invalid_responses(self):
        bad_outputs = [None, [], "null", "[1, 2]", "{broken", 42,
                       dict(INVOICE_242, items="rows"),
                       dict(INVOICE_242, items=[{}]),
                       dict(INVOICE_242, items=[{"description": "", "quantity": ""}]),
                       dict(INVOICE_242, customer="Chavez-Wilcox")]
        for output in bad_outputs:
            with self.subTest(output=str(output)[:40]):
                with self.assertRaises(InvalidModelResponse):
                    run(output)

    def test_row_without_readable_description_is_kept_with_a_finding(self):
        result = run(fixture(items=[{"quantity": "1,00", "unit_price": "6,08",
                                     "net_amount": "6,08", "gross_amount": "6,69"}]))
        self.assertIsNone(result["record"]["items"][0]["description"])
        missing = [f for f in result["findings"] if f["code"] == "item_missing_field"]
        self.assertEqual(missing[0]["field"], "items[0].description")

    def test_invalid_row_count_is_null_not_guessed(self):
        self.assertIsNone(run(fixture(row_count_seen=-1))["record"]["row_count_seen"])
        self.assertIsNone(run(fixture(row_count_seen="4"))["record"]["row_count_seen"])

    def test_currency_labels_are_ignored_but_noise_inside_numbers_is_not(self):
        from backend.app.extraction.schema import parse_amount
        self.assertEqual(parse_amount("$ 7.00"), 7)
        self.assertEqual(parse_amount("7,00 EUR"), 7)
        self.assertEqual(parse_amount("1 234,56"), Decimal("1234.56"))
        for noisy in ("69A,9O", "12-34", "1O0,00", "-5,00", "(5,00)"):
            self.assertIsNone(parse_amount(noisy), noisy)

    def test_unparseable_number_becomes_null_with_a_finding(self):
        result = run(fixture(total_amount="69A,9O"))
        self.assertIsNone(result["record"]["total_amount"])
        self.assertIn("unparseable_value", codes(result))
        self.assertEqual(result["record"]["field_evidence"]["total_amount"]["raw_text"], "69A,9O")


class ArithmeticTests(unittest.TestCase):
    def mutate(self, item_index=None, **changes):
        value = fixture()
        if item_index is None:
            value.update(changes)
        else:
            value["items"][item_index].update(changes)
        return value

    def assert_flagged(self, output, code, **where):
        result = run(output)
        matches = [item for item in result["findings"] if item["code"] == code]
        self.assertTrue(matches, f"{code} not in {codes(result)}")
        for key, expected in where.items():
            self.assertEqual(matches[0][key], expected)
        self.assertEqual(result["validation_status"], "arithmetic_mismatch")
        return result

    def test_each_arithmetic_check_flags_without_editing_printed_values(self):
        cases = [
            (self.mutate(1, quantity="6,00"), "arithmetic_line_net",
             {"row": 2, "field": "items[1].net_amount"}),
            (self.mutate(0, vat_amount="51,00"), "arithmetic_line_tax",
             {"row": 1, "field": "items[0].vat_amount"}),
            (self.mutate(0, gross_amount="560,00"), "arithmetic_line_gross",
             {"row": 1, "field": "items[0].gross_amount"}),
            (self.mutate(subtotal="641,73"), "arithmetic_sum_net", {"field": "subtotal"}),
            (self.mutate(tax_amount="64,17"), "arithmetic_sum_tax", {"field": "tax_amount"}),
            (self.mutate(total_amount="695,90"), "arithmetic_sum_gross",
             {"field": "total_amount"}),
        ]
        for output, code, where in cases:
            with self.subTest(code=code):
                result = self.assert_flagged(output, code, **where)
                # Values stay as printed (parsed), never adjusted to reconcile.
                self.assertEqual(result["record"]["items"][1]["quantity"],
                                 6.0 if code == "arithmetic_line_net" else 5.0)

    def test_gross_minus_net_versus_rate_when_line_vat_is_not_printed(self):
        output = fixture()
        for item in output["items"]:
            item.pop("vat_amount")
        output["items"][2]["gross_amount"] = "13,10"
        result = run(output)
        self.assertIn("arithmetic_line_tax_from_gross", codes(result))
        self.assertIsNone(result["record"]["items"][2]["vat_amount"])

    def test_invoice_total_check_and_no_sum_check_without_rows(self):
        findings = arithmetic_findings({"items": [], "subtotal": 10.0, "tax_amount": 1.0,
                                        "total_amount": 12.0})
        self.assertEqual([item["code"] for item in findings], ["arithmetic_invoice_total"])

    def test_tolerance_follows_printed_precision(self):
        record = {"items": [{"quantity": 3.0, "unit_price": 333.0, "net_amount": 1000.0}]}
        self.assertEqual(arithmetic_findings(record, precision=0), [])  # 999 vs 1000: 1 unit
        self.assertTrue(arithmetic_findings(record, precision=2))
        evidence = {"items[0].net_amount": {"raw_text": "1000"},
                    "total_amount": {"raw_text": "1000"}}
        self.assertEqual(money_precision(evidence), 0)
        self.assertEqual(money_precision({"total_amount": {"raw_text": "694,90"}}), 2)


class RowAndDocumentTests(unittest.TestCase):
    def test_missing_row_is_not_fabricated_and_parser_row_count_flags_it(self):
        output = fixture(items=INVOICE_242["items"][:3])
        result = run(output, ParseContext(text=PARSER_TEXT_242, table_row_count=4,
                                          engine="fixture-parser"))
        self.assertEqual(len(result["record"]["items"]), 3)
        self.assertIn("row_count_mismatch", codes(result))
        self.assertIn("arithmetic_sum_net", codes(result))
        self.assertEqual(result["validation_status"], "incomplete_extraction")
        self.assertEqual(result["provenance"]["parser"]["table_row_count"], 4)

    def test_model_self_count_is_checked_but_not_treated_as_independent(self):
        self.assertIn("row_count_self_report_mismatch",
                      codes(run(fixture(row_count_seen=5))))
        agreed = run(fixture(row_count_seen=4))
        self.assertIn("row_count_not_independent", codes(agreed))
        self.assertEqual(agreed["validation_status"], "valid_reconciled")

    def test_multipage_artefacts_are_flagged_not_dropped(self):
        rows = copy.deepcopy(INVOICE_242["items"])
        header = {"description": "Description Qty Net price Gross worth"}
        carried = {"description": "Balance b/f", "net_amount": "500,00", "page": 2}
        subtotal = {"description": "Page subtotal", "net_amount": "614,65"}
        wrapped = {"description": "cover English edition"}
        duplicate = dict(rows[1], page=2)
        output = fixture(items=rows[:2] + [subtotal, header, carried, duplicate, wrapped] + rows[2:])
        result = run(output)
        found = codes(result)
        for code in ("repeated_header_row", "carried_forward_row", "page_subtotal_row",
                     "duplicate_row_candidate", "possible_wrapped_description"):
            self.assertIn(code, found)
        self.assertEqual(len(result["record"]["items"]), 9)
        duplicate_finding = next(item for item in result["findings"]
                                 if item["code"] == "duplicate_row_candidate")
        self.assertEqual(duplicate_finding["details"]["duplicate_of_row"], 2)

    def test_non_invoice_is_retained_with_a_finding(self):
        result = run(fixture(doc_type="Purchase Order"))
        self.assertEqual(result["record"]["doc_type"], "purchase_order")
        self.assertIn("document_type_unexpected", codes(result))
        self.assertEqual(result["validation_status"], "ambiguous_review")

    def test_unknown_historical_amount_is_kept_and_flagged(self):
        output = fixture(items=[{"description": "Old row", "amount": "22,93"}])
        result = run(output)
        item = result["record"]["items"][0]
        self.assertEqual((item["amount"], item["amount_semantics"]), (22.93, "unknown"))
        self.assertIsNone(item["net_amount"])
        self.assertIn("legacy_amount_semantics_unknown", codes(result))

    def test_every_finding_code_has_a_defined_effect(self):
        effects = {"none", "ambiguous", "incomplete", "incomplete_rows", "mismatch"}
        self.assertTrue(set(CODE_EFFECTS.values()) <= effects)
        self.assertEqual(status_from_findings([]), "valid_reconciled")


class EvidenceTests(unittest.TestCase):
    def evidence_output(self, **evidence):
        return fixture(evidence=evidence)

    def test_quote_matching_tolerates_layout_unicode_ocr_and_locale(self):
        cases = [
            ("Invoice no: 95537278", "exact"),
            ("Day Trading Joe Ross New Hardcover English edition", "normalized"),  # wrap
            ("Invoice no: 95537278", "normalized"),  # non-breaking spaces
            ("ＩＢＡＮ: GB02TRCS19981598057841", "normalized"),  # full-width (NFKC)
            ("IBAN: GB02TRCS1998I598O57841", "ocr_folded"),  # I/1, O/0 confusions
            ("631.73", "normalized"),  # locale: printed as 631,73
            ("Rage By Bob Woodward - Donald Trump NEW HARDCOVER Sept 2O21", "fuzzy"),
            ("Acme Widgets Ltd", "not_found"),
            ("4", "too_short"),
        ]
        for quote, status in cases:
            with self.subTest(quote=quote):
                self.assertEqual(source_match(quote, PARSER_TEXT_242)["status"], status)

    def test_matching_evidence_produces_no_review_finding(self):
        output = self.evidence_output(**{
            "invoice_number": {"quote": "Invoice no: 95537278", "page": 1, "bbox": [10, 10, 200, 30]},
            "items.0.unit_price": {"quote": "125,00", "page": 1},
            "vendor.name": "Hart, Rios and Williams"})
        parse = ParseContext(pages=[{"page": 1, "text": PARSER_TEXT_242, "width": 1000,
                                     "height": 1400}])
        result = run(output, parse)
        evidence = result["record"]["field_evidence"]
        self.assertEqual(evidence["invoice_number"]["match"]["source"]["status"], "exact")
        self.assertTrue(evidence["items[0].unit_price"]["match"]["value_in_quote"])
        self.assertEqual(evidence["vendor.name"]["quote"], "Hart, Rios and Williams")
        self.assertNotIn("evidence_missing", codes(result))
        self.assertEqual(result["validation_status"], "valid_reconciled")

    def test_disagreeing_evidence_is_a_review_finding_and_never_a_correction(self):
        output = self.evidence_output(**{
            "items[1].unit_price": {"quote": "Net price 23,93", "page": 1},
            "invoice_number": {"quote": "Invoice no: 11111111", "page": 1}})
        result = run(output, ParseContext(text=PARSER_TEXT_242))
        found = codes(result)
        self.assertIn("value_not_in_quote", found)
        self.assertIn("evidence_not_in_source", found)
        self.assertEqual(result["record"]["items"][1]["unit_price"], 22.93)
        self.assertEqual(result["record"]["invoice_number"], "95537278")
        self.assertEqual(result["validation_status"], "ambiguous_review")

    def test_page_and_geometry_are_validated(self):
        parse = ParseContext(pages=[{"page": 1, "text": PARSER_TEXT_242, "width": 1000,
                                     "height": 1400}])
        output = self.evidence_output(**{
            "invoice_number": {"quote": "95537278", "page": 3},
            "invoice_date": {"quote": "11/10/2019", "page": 1, "bbox": [50, 10, 40, 30]},
            "subtotal": {"quote": "631,73", "page": 1, "bbox": [900, 1300, 1100, 1350]}})
        found = codes(run(output, parse))
        self.assertIn("evidence_page_invalid", found)
        self.assertEqual(found.count("evidence_geometry_invalid"), 2)
        self.assertIsNone(validate_geometry([[0.1, 0.1], [0.4, 0.1], [0.4, 0.2]], 1, parse))
        self.assertIsNone(validate_geometry([0.1, 0.2, 0.3, 0.4], 1, None))

    def test_numeric_value_in_quote_respects_locale(self):
        self.assertTrue(value_in_quote("items[0].net_amount", 1234.56, "Net 1.234,56 EUR"))
        self.assertTrue(value_in_quote("items[0].net_amount", 1234.56, "Net 1 234,56"))
        self.assertFalse(value_in_quote("items[0].net_amount", 1234.56, "Net 1.243,56"))
        self.assertIsNone(value_in_quote("items[0].net_amount", 1234.56, None))

    def test_evidence_list_form_and_aliases_are_accepted(self):
        record = run(fixture())["record"]
        raw = {"evidence": [{"field": "items.2.description", "quote": "Florida's List"}]}
        entries, _ = build_field_evidence(record, dict(INVOICE_242, **raw), False)
        self.assertEqual(entries["items[2].description"]["quote"], "Florida's List")


if __name__ == "__main__":
    unittest.main()
