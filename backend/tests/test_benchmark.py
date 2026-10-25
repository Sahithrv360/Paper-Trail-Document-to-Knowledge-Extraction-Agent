"""Milestone 4: evaluation harness (statistics, fairness, leakage guards).

Uses a synthetic one-template split built from the supplied fixture; no dataset or
model download is needed. These tests check the harness, not any model's accuracy.
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from backend.app.config import Settings
from backend.app.extraction import benchmark
from backend.app.extraction.providers import FakeProvider
from test_invoice_242 import INVOICE_242

FIXTURE = Path(__file__).parent / "fixtures" / "invoice-242.jpg"
GROUND_TRUTH = {"gt_parse": {
    "header": {"invoice_no": "95537278", "invoice_date": "11/10/2019",
               "seller": "Hart, Rios and Williams 6637 Mack Junction Apt. 855 Ericport, AR 84395",
               "client": "Chavez-Wilcox 7844 Carlson Expressway South Edwinborough, PA 19234",
               "seller_tax_id": "907-77-4589", "client_tax_id": "910-70-6111",
               "iban": "GB02TRCS19981598057841"},
    "items": [{"item_desc": item["description"].replace("\n", " "), "item_qty": item["quantity"],
               "item_net_price": item["unit_price"], "item_net_worth": item["net_amount"],
               "item_vat": item["tax_rate_percent"], "item_gross_worth": item["gross_amount"]}
              for item in INVOICE_242["items"]],
    "summary": {"total_net_worth": "$ 631,73", "total_vat": "$ 63,17",
                "total_gross_worth": "$ 694,90"}}}


def make_split(root: Path, split: str, rows: int):
    folder = root / split
    folder.mkdir(parents=True)
    manifest = []
    for index in range(rows):
        name = f"invoice-{index:03d}.jpg"
        shutil.copy(FIXTURE, folder / name)
        manifest.append({"row_index": index, "file": name, "ground_truth": json.dumps(GROUND_TRUTH)})
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class Loader:
    """Avoids decoding images: the fake provider ignores pixels."""
    def __call__(self, path):
        return type("Doc", (), {"pages": [object()], "first_image": lambda self: None,
                                "summary": lambda self: {}})()


class StatisticsTests(unittest.TestCase):
    def test_wilson_interval(self):
        result = benchmark.wilson(5, 10)
        self.assertEqual((result["estimate"], result["low"], result["high"]), (0.5, 0.2366, 0.7634))
        self.assertEqual(benchmark.wilson(0, 0)["estimate"], None)
        self.assertEqual(benchmark.wilson(10, 10)["high"], 1.0)
        self.assertLess(benchmark.wilson(10, 10)["low"], 1.0)

    def test_bootstrap_is_seeded_and_brackets_the_estimate(self):
        data = [(1, 2), (2, 2), (0, 2), (2, 2), (1, 2)]
        a = benchmark.bootstrap(data, benchmark._micro)
        self.assertEqual(a, benchmark.bootstrap(data, benchmark._micro))
        self.assertLessEqual(a["low"], a["estimate"])
        self.assertGreaterEqual(a["high"], a["estimate"])

    def test_split_party_is_not_penalised_on_label_fields(self):
        from backend.app.extraction.schema import normalize_donut_result
        expected = normalize_donut_result(GROUND_TRUTH).to_dict()
        scores = benchmark.document_scores(dict(INVOICE_242, items=[], vendor=INVOICE_242["vendor"],
                                                customer=INVOICE_242["customer"]), expected)
        self.assertFalse(scores["vendor"]["exact"])  # legacy field: name vs combined label
        self.assertTrue(scores["vendor_label"]["normalized"])
        self.assertTrue(scores["customer_label"]["normalized"])

    def test_normalized_item_matching_ignores_wraps_and_punctuation(self):
        predicted = [{"description": "Day Trading Joe Ross New Hard\ncover English edition",
                      "gross_amount": 550.0}]
        expected = [{"description": "Day Trading Joe Ross New Hardcover English edition",
                     "gross_amount": 550.0}]
        self.assertEqual(benchmark.normalized_item_counts(predicted, expected), (1, 1, 1))


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "data"
        make_split(self.root, "validation", 4)
        make_split(self.root, "test", 2)
        make_split(self.root, "train", 2)

    def run_fake(self, outputs, split="validation"):
        return benchmark.run_benchmark(FakeProvider(outputs), Settings.from_env({}), split,
                                       self.root, document_loader=Loader())

    def test_report_contains_rows_outputs_metrics_and_intervals(self):
        wrong = dict(INVOICE_242, invoice_number="95537279")
        report = self.run_fake([INVOICE_242, INVOICE_242, wrong, "not json"])
        agg = report["aggregate"]
        self.assertEqual(report["selected_row_indices"], [0, 1, 2, 3])
        self.assertEqual(agg["invalid_response_rate"]["estimate"], 0.25)
        self.assertEqual(agg["fields"]["invoice_number"]["exact"]["estimate"], 0.5)
        self.assertEqual(agg["document_accuracy_normalized"]["estimate"], 0.5)
        self.assertEqual(agg["line_items_normalized"]["recall"]["estimate"], 0.75)
        self.assertEqual(agg["review_flags"]["wrong_but_not_flagged"], 1)
        document = report["documents"][0]
        self.assertEqual(document["source"]["file"], "invoice-000.jpg")
        self.assertIn("output", document)
        self.assertEqual(document["expected"]["invoice_number"], "95537278")
        self.assertEqual(report["config"]["provider"], "fake")
        self.assertIn("process_peak_mb", report["runtime"])
        self.assertIn("Micro field accuracy", benchmark.markdown(report))

    def test_paired_comparison_on_identical_rows(self):
        a = self.run_fake([dict(INVOICE_242, invoice_number="x")] * 4)
        b = self.run_fake([INVOICE_242] * 4)
        result = benchmark.compare(a, b)
        self.assertGreater(result["micro_field_accuracy_normalized_difference"]["estimate"], 0)
        self.assertEqual(result["documents_better_worse_same"], [4, 0, 0])
        partial = dict(b, documents=b["documents"][:2])
        with self.assertRaises(ValueError):
            benchmark.compare(a, partial)

    def test_test_split_needs_a_matching_frozen_config_and_runs_once(self):
        log = Path(self._tmp.name) / "test-runs.jsonl"
        validation_report = Path(self._tmp.name) / "val.json"
        original = benchmark.run_benchmark

        def fake_run(provider, settings, split, data_root, limit, parser, version, retriever, rag_k):
            replay = FakeProvider([INVOICE_242] * (4 if split == "validation" else 2))
            replay.name, replay.revision = provider.name, provider.revision  # same fingerprint
            return original(replay, settings, split, data_root, limit, None, version, retriever,
                            rag_k, document_loader=Loader())

        def invoke(args):
            out = io.StringIO()
            with mock.patch.object(benchmark, "TEST_RUN_LOG", log), \
                    mock.patch.dict("os.environ", {"PARSER_ENGINE": ""}), \
                    mock.patch("backend.app.extraction.benchmark.run_benchmark", side_effect=fake_run), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                return benchmark.main(args)

        common = ["run", "--provider", "ocr", "--parser", "tesseract", "--data-root", str(self.root)]
        with mock.patch("backend.app.extraction.local_ocr.build_engine",
                        return_value=type("E", (), {"version": "fake-ocr"})()):
            with self.assertRaises(SystemExit):
                invoke(common + ["--split", "test", "--output", str(Path(self._tmp.name) / "t.json")])
            self.assertEqual(invoke(common + ["--output", str(validation_report)]), 0)
            frozen = Path(self._tmp.name) / "frozen.json"
            self.assertEqual(invoke(["freeze", str(validation_report), "--output", str(frozen)]), 0)
            test_args = common + ["--split", "test", "--final-test-run", "--frozen", str(frozen),
                                  "--output", str(Path(self._tmp.name) / "t.json")]
            self.assertEqual(invoke(test_args), 0)
            self.assertEqual(len(log.read_text().splitlines()), 1)
            with self.assertRaises(SystemExit):
                invoke(test_args)  # second test run of the same configuration
            with self.assertRaises(SystemExit):
                invoke(common + ["--split", "test", "--final-test-run", "--frozen", str(frozen),
                                 "--limit", "1", "--output", str(Path(self._tmp.name) / "t2.json")]
                       + ["--rag"])  # rag is refused for non-text providers

    def test_rag_retriever_refuses_held_out_splits(self):
        from backend.app.extraction.rag import TrainingRetriever
        with self.assertRaises(ValueError):
            TrainingRetriever([], split="validation")


if __name__ == "__main__":
    unittest.main()
