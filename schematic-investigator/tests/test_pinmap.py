import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from schematic_investigator.benchmark import evaluate
from schematic_investigator.cli import main
from schematic_investigator.core import Investigator, fingerprint, index_pdf
from schematic_investigator.pinmap import pinmap, markdown_table
from fixtures import make_review_pdf


class PinmapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = make_review_pdf(self.root / "review.pdf")
        self.db = self.root / "review.sqlite"
        index_pdf(self.pdf, self.db)

    def run_map(self, **kwargs):
        options = dict(left_page=1, right_page=2, left_connector="J1", right_connector="J9",
                       pins=["A13", "B48", "A1"], left_bbox=[0, 70, 470, 205], right_bbox=[0, 70, 470, 205],
                       left_net_side="left", right_net_side="left")
        options.update(kwargs)
        return pinmap(self.db, self.db, **options)

    def test_exact_pins_scope_citations_and_unknown_electrical_properties(self):
        result = self.run_map()
        expected = [("PG_HOST", "STBY_GPIO"), ("DATA_HOST", "RMII_DATA0"), ("SUPPLY_HOST", "SUPPLY_CARD")]
        with Investigator(self.db) as inv:
            for row, labels in zip(result["rows"], expected):
                self.assertEqual(row["connection_status"], "requires_visual_review")
                for side, label in zip(("left", "right"), labels):
                    endpoint = row[side]
                    self.assertEqual(endpoint["total_pin_occurrences"], 1)
                    self.assertEqual(endpoint["net_candidates"][0]["net_labels"], [label])
                    self.assertTrue(inv.citation_valid(endpoint["net_candidates"][0]["citation"]))
                    self.assertTrue(inv.citation_valid(endpoint["pin_occurrences"][0]["citation"]))
                    for property_ in ("assembly", "direction", "voltage_domain"):
                        self.assertEqual(endpoint[property_], "unknown")
        self.assertEqual(result["claims"], [])
        unscoped = self.run_map(left_bbox=None)
        self.assertEqual(unscoped["rows"][0]["left"]["total_pin_occurrences"], 2)
        self.assertEqual(unscoped["documents"]["left"]["scope"], "whole_page_unscoped")

    def test_rotated_or_missing_pins_do_not_invent_connections(self):
        result = self.run_map(pins=["B26", "Z99"], left_bbox=None, right_bbox=None)
        for side in ("left", "right"):
            self.assertEqual(result["rows"][0][side]["total_pin_occurrences"], 1)
            self.assertEqual(result["rows"][0][side]["net_candidates"], [])
            self.assertEqual(result["rows"][1][side]["status"], "pin_text_not_found_in_scope")

    def test_different_and_mirrored_pin_pairs_are_explicit_and_one_to_one(self):
        result = self.run_map(pins=["A1"], pin_pairs=[("A13", "B48"), ("B48", "A13")])
        self.assertEqual([(r["left_pin"], r["right_pin"]) for r in result["rows"]],
                         [("A1", "A1"), ("A13", "B48"), ("B48", "A13")])
        self.assertEqual(result["rows"][1]["right"]["net_candidates"][0]["net_labels"], ["RMII_DATA0"])
        for pairs in [[("A13", "B48"), ("a13", "A1")], [("A13", "B48"), ("A1", "b48")]]:
            with self.assertRaisesRegex(ValueError, "one-to-one"):
                self.run_map(pins=None, pin_pairs=pairs)
        with self.assertRaises(ValueError):
            self.run_map(pins=None, pin_pairs=[("../A1", "B48")])

    def test_cli_different_pin_pair_and_missing_right_pin(self):
        base = ["--db", str(self.db), "pinmap", "--other-db", str(self.db),
                "--left-page", "1", "--right-page", "2", "--left-connector", "J1",
                "--right-connector", "J9", "--pin-pair", "A13", "Z99", "--output", str(self.root / "pairs.json")]
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(base), 0)
        self.assertEqual(json.loads(stdout.getvalue())["pin_text_missing"], [{"pin": "Z99", "side": "right"}])

    def test_opposite_bank_same_row_decoy_is_candidate_until_filtered(self):
        unscoped = self.run_map(left_bbox=None, right_bbox=None, left_net_side="either", right_net_side="either")
        for side in ("left", "right"):
            endpoint = unscoped["rows"][0][side]
            self.assertIn("DECOY_NET", [name for c in endpoint["net_candidates"] for name in c["net_labels"]])
            self.assertTrue(all(c["status"] == "unreviewed_spatial_candidate" for c in endpoint["net_candidates"]))
        filtered = self.run_map(left_bbox=None, right_bbox=None)
        for side in ("left", "right"):
            self.assertNotIn("DECOY_NET", [name for c in filtered["rows"][0][side]["net_candidates"] for name in c["net_labels"]])

    def test_limits_and_crops_preserve_pdf(self):
        before = fingerprint(self.pdf)
        result = self.run_map(left_bbox=None, right_bbox=None, max_candidates=1,
                              max_occurrences=1, crops_dir=self.root / "crops")
        self.assertEqual(result["rows"][0]["left"]["omitted_pin_occurrences"], 1)
        self.assertEqual(result["rows"][0]["left"]["omitted_net_candidates"], 1)
        for row in result["rows"]:
            for side in ("left", "right"):
                self.assertTrue(Path(row[side]["crop"]["output"]).is_file())
        self.assertEqual(before, fingerprint(self.pdf))
        text = markdown_table(result)
        self.assertIn("requires visual review", text)
        self.assertIn(before, text)

    def test_invalid_requests_and_changed_source_rejected(self):
        for options in [dict(pins=[]), dict(pins=["A13", "a13"]), dict(pins=["../A1"]),
                        dict(left_page=99), dict(left_bbox=[0, 0, float('nan'), 40]),
                        dict(max_candidates=0)]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_map(**options)
        with self.pdf.open("ab") as file:
            file.write(b"\n%changed\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_map()

    def test_cli_saves_table_and_prevents_source_or_index_overwrite(self):
        base = ["--db", str(self.db), "pinmap", "--other-db", str(self.db),
                "--left-page", "1", "--right-page", "2", "--left-connector", "J1",
                "--right-connector", "J9", "--pin", "A13"]
        output, md = self.root / "map.json", self.root / "map.md"
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main([*base, "--output", str(output), "--markdown", str(md)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["rows"], 1)
        self.assertEqual(json.loads(output.read_text())["rows"][0]["pin"], "A13")
        self.assertTrue(md.is_file())
        before = fingerprint(self.pdf), fingerprint(self.db)
        for target in [self.pdf, self.db]:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([*base, "--output", str(target)]), 2)
        self.assertEqual(before, (fingerprint(self.pdf), fingerprint(self.db)))

    def test_electrical_trap_reports_require_review_and_reject_overclaims(self):
        config = json.loads((Path(__file__).parents[1] / "benchmarks" / "electrical-review.json").read_text())
        with Investigator(self.db) as inv:
            self.assertFalse(evaluate(inv, config)["passed"])
            citation = inv.search("R1", "exact")["pages"][0]["hits"][0]["citation"]
            reports = {case["id"]: {"citations": [citation], "claims": []} for case in config["cases"]}
            self.assertTrue(evaluate(inv, config, reports)["passed"])
            for forbidden in config["cases"][0]["forbidden_unsupported_conclusions"]:
                reports["pull-resistor-endpoint"]["claims"] = [
                    {"text": forbidden, "kind": "observation", "status": "confirmed", "citations": [citation]}]
                self.assertFalse(evaluate(inv, config, reports)["passed"])
            reports["pull-resistor-endpoint"]["claims"] = [
                {"text": "Connection claimed from proximity", "kind": "connectivity", "status": "confirmed", "citations": [citation]}]
            self.assertFalse(evaluate(inv, config, reports)["passed"])


if __name__ == "__main__":
    unittest.main()
