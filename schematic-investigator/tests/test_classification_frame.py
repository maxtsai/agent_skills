import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pymupdf as fitz

from schematic_investigator.classification import classify_text
from schematic_investigator.comparison import compare_indexes, compact_comparison
from schematic_investigator.core import Investigator, index_pdf
from schematic_investigator.pinmap import pinmap


class ClassificationFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_labels_keep_annotations_values_and_assembly_separate(self):
        labels = {x["text"]: x["kind"] for x in classify_text(
            "DATA_HOST CLK_NETCLASS_STATUS DIFFPAIR_ALERT 50OHM_NETCLASS1 R1 4.7k 0ohm 0.1uF NO STUFF COMMON DNI")}
        self.assertEqual(labels["DATA_HOST"], "net")
        self.assertEqual(labels["50OHM_NETCLASS1"], "annotation")
        self.assertEqual(labels["CLK_NETCLASS_STATUS"], "net")
        self.assertEqual(labels["DIFFPAIR_ALERT"], "net")
        for value in ["4.7k", "0ohm", "0.1uF"]:
            self.assertEqual(labels[value], "component_value")
        for value in ["NO STUFF", "COMMON", "DNI"]:
            self.assertEqual(labels[value], "assembly")

    def test_old_index_reclassified_without_losing_text_or_citations(self):
        pdf, db = self.root / "labels.pdf", self.root / "labels.sqlite"
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=400)
            page.insert_text((400, 100), "A13")
            page.insert_text((330, 100), "50OHM_NETCLASS1", fontsize=7)
            page.insert_text((100, 100), "REAL_NET")
            page.insert_text((230, 100), "NO STUFF", fontsize=9)
            doc.save(pdf)
        index_pdf(pdf, db)
        # Emulate 0.3 indexes whose entity classifier treated this as a net.
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE evidence SET entities=? WHERE text=?",
                               (json.dumps([{"text": "50OHM_NETCLASS1", "kind": "net", "candidate": True}]), "50OHM_NETCLASS1"))
        result = pinmap(db, db, 1, 1, "J1", "J2", ["A13"], left_net_side="left", right_net_side="left")
        endpoint = result["rows"][0]["left"]
        self.assertEqual([c["net_labels"] for c in endpoint["net_candidates"]], [["REAL_NET"]])
        all_other = [label for c in endpoint["other_text_candidates"] for label in c["classified_labels"]]
        self.assertIn({"text": "50OHM_NETCLASS1", "kind": "annotation", "reason": "drawing/net-class rule text", "candidate": True}, all_other)
        with Investigator(db) as inv:
            for c in endpoint["other_text_candidates"]:
                self.assertTrue(inv.citation_valid(c["citation"]))
            self.assertEqual(inv.search("50OHM_NETCLASS1", "exact")["pages"][0]["hits"][0]["text"], "50OHM_NETCLASS1")

    def frame_pdf(self, name, date, value):
        pdf, db = self.root / f"{name}.pdf", self.root / f"{name}.sqlite"
        with fitz.open() as doc:
            for title, circuit_value in [("FRAME ONLY", None), ("CIRCUIT CHANGE", value)]:
                p = doc.new_page(width=600, height=500)
                p.insert_text((40, 40), title, fontsize=20)
                p.insert_text((40, 480), date)
                if circuit_value:
                    # A real circuit value at the page bottom must remain visible.
                    p.insert_text((300, 480), circuit_value)
                    p.insert_text((300, 460), "R1")
                # A date-looking string in the circuit body must not be hidden.
                p.insert_text((60, 150), "2026-10-21")
            doc.save(pdf)
        index_pdf(pdf, db)
        return db

    def test_frame_only_changes_separated_and_bottom_component_value_kept(self):
        a = self.frame_pdf("old", "4-AUG-2025", "10k")
        b = self.frame_pdf("new", "14-MAY-2026", "4.7k")
        result = compare_indexes(a, b)
        self.assertEqual(result["total_changed_pairs"], 2)
        self.assertEqual(result["total_circuit_changed_pairs"], 1)
        self.assertEqual(result["frame_only_pairs"][0]["old_page"], 1)
        compact = compact_comparison(result)
        self.assertEqual(len(compact["changed_pairs"]), 1)
        change = compact["changed_pairs"][0]
        self.assertEqual(change["old_page"], 2)
        self.assertEqual([c["text"] for c in change["added"]], ["4.7k"])
        all_text = compact_comparison(result, show_frame=True)
        self.assertEqual(len(all_text["changed_pairs"]), 2)
        with Investigator(a) as inv:
            for pair in result["changed_pairs"]:
                self.assertTrue(all(inv.citation_valid(c) for c in pair["removed"]))
                self.assertTrue(all(inv.citation_valid(c["citation"]) for c in pair["frame_removed"]))

    def test_date_text_in_circuit_body_is_not_frame_metadata(self):
        databases = []
        for name, date in [("body-old", "4-AUG-2025"), ("body-new", "14-MAY-2026")]:
            pdf, db = self.root / f"{name}.pdf", self.root / f"{name}.sqlite"
            with fitz.open() as doc:
                page = doc.new_page(width=600, height=500)
                page.insert_text((40, 40), "BODY DATE", fontsize=20)
                page.insert_text((100, 200), date)
                doc.save(pdf)
            index_pdf(pdf, db)
            databases.append(db)
        result = compare_indexes(*databases)
        self.assertEqual(result["total_circuit_changed_pairs"], 1)
        self.assertEqual(result["changed_pairs"][0]["frame_added"], [])

    def test_numeric_alias_and_annotation_in_joined_pin_line(self):
        databases = []
        for name, pin in [("alias-left", "A13"), ("alias-right", "27")]:
            pdf, db = self.root / f"{name}.pdf", self.root / f"{name}.sqlite"
            with fitz.open() as doc:
                page = doc.new_page(width=600, height=400)
                page.insert_text((80, 100), f"{pin} SIGNAL_OK NO STUFF 4.7k")
                doc.save(pdf)
            index_pdf(pdf, db)
            databases.append(db)
        result = pinmap(*databases, 1, 1, "J1", "J2", pin_pairs=[("A13", "27")])
        row = result["rows"][0]
        self.assertEqual(row["right"]["pin"], "27")
        self.assertEqual(row["right"]["net_candidates"][0]["net_labels"], ["SIGNAL_OK"])
        self.assertTrue(row["right"]["other_text_candidates"])


if __name__ == "__main__":
    unittest.main()
