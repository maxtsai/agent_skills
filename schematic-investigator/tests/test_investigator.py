import contextlib
import io
import json
import os
import re
from pathlib import Path
import tempfile
import unittest
import pymupdf as fitz

from schematic_investigator.core import Investigator, index_pdf
from schematic_investigator.cli import main
from schematic_investigator.benchmark import evaluate
from fixtures import make_pdf


class InvestigationTests(unittest.TestCase):
    def test_compact_cli_preserves_citations_and_full_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "index.sqlite"
            index_pdf(make_pdf(root / "board.pdf"), db)
            output = root / "evidence.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--db", str(db), "search", "BMC I2C", "--compact",
                             "--max-hits", "1", "--output", str(output)])
            self.assertEqual(code, 0)
            compact = json.loads(stdout.getvalue())
            full = json.loads(output.read_text())
            self.assertLess(len(stdout.getvalue()), len(output.read_text()))
            with Investigator(db) as inv:
                for short, complete in zip(compact["pages"], full["pages"]):
                    self.assertNotIn("regions", short)
                    self.assertEqual(short["omitted_hits"], len(complete["hits"]) - 1)
                    self.assertTrue(inv.citation_valid(short["hits"][0]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = make_pdf(self.root / "board.pdf")
        self.db = self.root / "index.sqlite"
        index_pdf(self.pdf, self.db)
        self.inv = Investigator(self.db)

    def tearDown(self):
        self.inv.close()
        self.temp.cleanup()

    def test_index_provenance_geometry_and_split_spans(self):
        self.assertEqual(self.inv.c.execute("SELECT count(*) FROM pages").fetchone()[0], 9)
        self.assertIn("Synthetic", self.inv.doc["metadata"])
        hit = self.inv.search("BMC_RESET_N", "exact", details=True)["pages"][0]["hits"][0]
        self.assertGreaterEqual(len(hit["spans"]), 2)
        self.assertEqual(hit["page"], 2)
        self.assertEqual(len(hit["bbox"]), 4)
        self.assertTrue(self.inv.citation_valid(hit["citation"]))
        rotated = self.inv.search("U77", "exact")["pages"][0]
        self.assertEqual(rotated["rotation"], 90)
        self.assertEqual(rotated["hits"][0]["direction"], [0, -1])

    def test_retrieval_modes_and_coverage(self):
        for mode, term in [("exact", "U23"), ("regex", r"BMC_I2C_SD[A]"),
                           ("wildcard", "BMC_I2C_*"), ("fuzzy", "PCA954B"), ("fts", "BMC I2C DEBUG")]:
            with self.subTest(mode=mode):
                self.assertEqual(self.inv.search(term, mode)["pages"][0]["page"], 2)
        self.assertEqual(self.inv.search("BMC I2C")["pages"][0]["page"], 2)
        self.assertFalse(self.inv.search("U2", "exact")["pages"])
        expanded = self.inv.search("management bus hangs", expansions=["BMC_I2C_SDA"])
        self.assertEqual(expanded["pages"][0]["page"], 2)

    def test_neighborhood_is_bounded(self):
        results = self.inv.search("U23", "exact", radius=70)
        nearby = results["pages"][0]["regions"][0]["evidence"]
        self.assertIn("BMC_I2C_SDA", [h["text"] for h in nearby])
        self.assertNotIn("UNRELATED_NET", [h["text"] for h in nearby])
        self.assertEqual(results["claims"], [])

    def test_crop_rotated_and_refdes(self):
        for page, ref in [(2, "U23"), (3, "U77")]:
            crop = self.inv.crop(page, self.root / f"{ref}.png", refdes=ref, margin=40, dpi=144)
            pix = fitz.Pixmap(crop["output"])
            self.assertAlmostEqual(pix.width, (crop["bbox"][2]-crop["bbox"][0])*2, delta=2)
            self.assertGreater(len(crop["anchors"]), 0)
        with self.assertRaises(ValueError):
            self.inv.crop(2, self.root / "bad.png", bbox=[5000, 0, 5100, 10])
        with self.assertRaises(ValueError):
            self.inv.crop(2, self.root / "bad.png", refdes="U77")

    def test_invalid_inputs(self):
        for query, mode in [("", "auto"), ("[", "regex")]:
            with self.assertRaises((ValueError, re.error)):
                self.inv.search(query, mode)
        self.assertFalse(self.inv.citation_valid({"evidence_id": -1}))
        with self.assertRaises(ValueError):
            self.inv.search("U23", radius=float('nan'))
        with self.assertRaises(ValueError):
            self.inv.crop(0, self.root / "bad.png")

    def test_fts_tokenization_and_report_validation(self):
        pages = self.inv.search("BMC I2C SDA", "fts")["pages"]
        self.assertEqual(pages[0]["page"], 2)
        self.assertIn("BMC_I2C_SDA", [h["text"] for h in pages[0]["hits"]])
        with self.assertRaises(ValueError):
            evaluate(self.inv, self.config(), {"bus": {}})
        malformed = {"bus": {"citations": [], "claims": [
            {"text": "something", "kind": "connectivity", "status": "confirmd", "citations": []}]}}
        with self.assertRaises(ValueError):
            evaluate(self.inv, self.config(), malformed)

    def test_atomic_rebuild_and_stale_source(self):
        corrupt = self.root / "bad.pdf"
        corrupt.write_bytes(b"not a pdf")
        with self.assertRaises(Exception):
            index_pdf(corrupt, self.db)
        self.assertEqual(self.inv.search("U23", "exact")["pages"][0]["page"], 2)
        with self.pdf.open("ab") as f:
            f.write(b"\n%modified\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.inv.crop(2, self.root / "bad.png")

    def config(self):
        return {"version": 1, "cases": [{"id": "bus", "query": "BMC I2C",
                "expected": {"pages": [2], "components": ["U23"], "nets": ["BMC_I2C_SDA"],
                             "regions": [{"page": 2, "bbox": [120, 80, 430, 360]}]},
                "forbidden_unsupported_conclusions": ["U23 is connected to U77"]}]}

    def test_benchmark_and_report_failures(self):
        result = evaluate(self.inv, self.config())
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["metrics"]["recall_at_5"], 1)
        self.assertEqual(result["metrics"]["citation_validity"], 1)
        self.assertIsNone(result["metrics"]["unsupported_connectivity_claims"])
        citation = self.inv.search("U23", "exact")["pages"][0]["hits"][0]["citation"]
        bad = dict(citation, page=1)
        reports = {"bus": {"citations": [bad], "claims": [
            {"text": "U23 is connected to U77", "kind": "connectivity", "status": "confirmed", "citations": [citation]}]}}
        failed = evaluate(self.inv, self.config(), reports)
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["metrics"]["unsupported_connectivity_claims"], 1)
        self.assertLess(failed["metrics"]["citation_validity"], 1)
        reports["bus"]["claims"][0]["status"] = "hypothesis"
        reports["bus"]["citations"] = [citation]
        self.assertTrue(evaluate(self.inv, self.config(), reports)["passed"])

    def test_alternative_and_failed_recall(self):
        config = self.config()
        config["cases"][0]["expected"] = {"pages": [999]}
        self.assertFalse(evaluate(self.inv, config)["passed"])
        config["cases"][0]["alternatives"] = [{"pages": [2]}]
        self.assertTrue(evaluate(self.inv, config)["passed"])
        config["require_reports"] = True
        self.assertFalse(evaluate(self.inv, config)["passed"])

    def test_sample_benchmark_and_wrong_region(self):
        sample = Path(__file__).resolve().parents[1] / "benchmarks" / "synthetic.json"
        result = evaluate(self.inv, json.loads(sample.read_text()))
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["metrics"]["region_accuracy"], 1)
        config = self.config()
        config["cases"][0]["expected"]["regions"] = [{"page": 2, "bbox": [500, 440, 590, 490]}]
        failed = evaluate(self.inv, config)
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["metrics"]["region_accuracy"], 0)

    def test_cli(self):
        for command in [["search", "BMC I2C"], ["locate", "U23"],
                        ["crop", "--page", "2", "--refdes", "U23", "--output", str(self.root / "cli.png")]]:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--db", str(self.db), *command]), 0)
                self.assertIsInstance(json.loads(output.getvalue()), dict)
        cfg = self.root / "tests.json"
        cfg.write_text(json.dumps(self.config()))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", str(self.db), "benchmark", str(cfg)]), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--db", str(self.db), "crop", "--page", "99"]), 2)


@unittest.skipUnless(os.environ.get("SCHEMATIC_TEST_PDF"), "Set SCHEMATIC_TEST_PDF for real schematic smoke test")
class RealPDFTest(unittest.TestCase):
    def test_real_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "real.sqlite"
            stats = index_pdf(os.environ["SCHEMATIC_TEST_PDF"], db)
            self.assertGreater(stats["evidence_count"], 0)
            with Investigator(db) as inv:
                row = inv.c.execute("SELECT * FROM evidence LIMIT 1").fetchone()
                evidence = inv.evidence(row)
                self.assertTrue(inv.citation_valid(evidence["citation"]))
                crop = inv.crop(row["page"], Path(temp)/"real.png", bbox=evidence["bbox"], dpi=144)
                self.assertGreater(crop["width"], 0)


if __name__ == "__main__":
    unittest.main()
