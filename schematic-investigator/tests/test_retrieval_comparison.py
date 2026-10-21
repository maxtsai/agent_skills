import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz

from schematic_investigator.cli import main
from schematic_investigator.comparison import compare_indexes, compact_comparison
from schematic_investigator.core import Investigator, fingerprint, index_pdf
from fixtures import make_pdf


def revision(path, sheets):
    with fitz.open() as doc:
        for title, labels in sheets:
            page = doc.new_page(width=600, height=400)
            page.insert_text((40, 40), title, fontsize=20)
            for i, label in enumerate(labels):
                page.insert_text((70, 100+i*25), label)
        doc.save(path)
    return path


class RetrievalComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = make_pdf(self.root / "fixture.pdf")
        self.db = self.root / "fixture.sqlite"
        index_pdf(self.pdf, self.db)

    def test_page_filter_applies_before_ranking_and_reports_omissions(self):
        with Investigator(self.db) as inv:
            selected = inv.search("BMC", "exact", pages=[8, 9], top=1, include_regions=False)
            self.assertIn(selected["pages"][0]["page"], [8, 9])
            self.assertEqual(selected["total_matching_pages"], 2)
            self.assertEqual(selected["omitted_pages"], 1)
            for pages in [[], [0], [999]]:
                with self.assertRaises(ValueError):
                    inv.search("BMC", pages=pages)

    def test_compact_cli_skips_neighborhoods_but_output_retains_them(self):
        with patch.object(Investigator, "neighborhood", side_effect=AssertionError("unneeded work")):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(["--db", str(self.db), "locate", "U23", "--page", "2", "--compact"]), 0)
            compact = json.loads(stdout.getvalue())
            self.assertFalse(compact["regions_included"])
            citation = compact["pages"][0]["hits"][0]
        output = self.root / "full.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", str(self.db), "locate", "U23", "--compact", "--output", str(output)]), 0)
        self.assertTrue(json.loads(output.read_text())["pages"][0]["regions"])
        with Investigator(self.db) as inv:
            expanded = inv.inspect_evidence(citation["evidence_id"], radius=80, max_neighbors=1)
            self.assertEqual(expanded["evidence"], citation)
            self.assertEqual(len(expanded["neighbors"]), 1)
            self.assertGreater(expanded["omitted_neighbors"], 0)
            self.assertTrue(inv.citation_valid(expanded["neighbors"][0]))
            with self.assertRaises(ValueError):
                inv.inspect_evidence(-1)

    def test_stale_or_missing_pdf_rejected_by_search_and_evidence(self):
        with self.pdf.open("ab") as f:
            f.write(b"\n%changed\n")
        with Investigator(self.db) as inv:
            for operation in [lambda: inv.search("U23"), lambda: inv.inspect_evidence(1)]:
                with self.assertRaisesRegex(ValueError, "changed"):
                    operation()
        for command in [["status"], ["search", "U23"], ["evidence", "1"]]:
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(["--db", str(self.db), *command]), 2)
                self.assertIn("changed", json.loads(stderr.getvalue())["error"])
        self.pdf.unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--db", str(self.db), "status"]), 2)

    def pair_fixture(self):
        old_pdf = revision(self.root / "old.pdf", [
            ("POWER", ["R1", "10k", "COMMON", "SIGNAL", "SIGNAL"]),
            ("DUPLICATE", ["R2"]), ("DUPLICATE", ["R3"])])
        new_pdf = revision(self.root / "new.pdf", [
            ("INSERTED", ["R9"]), ("POWER", ["R1", "4.7k", "NO STUFF", "SIGNAL"]),
            ("DUPLICATE", ["R2"]), ("DUPLICATE", ["R3"])])
        old_db, new_db = self.root / "old.sqlite", self.root / "new.sqlite"
        index_pdf(old_pdf, old_db)
        index_pdf(new_pdf, new_db)
        return old_pdf, new_pdf, old_db, new_db

    def test_comparison_handles_inserted_pages_duplicate_titles_and_counts(self):
        a, b, old_db, new_db = self.pair_fixture()
        hashes = fingerprint(a), fingerprint(b)
        result = compare_indexes(old_db, new_db, crops_dir=self.root / "crops")
        self.assertEqual(result["unpaired_old_pages"], [2, 3])
        self.assertEqual(result["unpaired_new_pages"], [1, 3, 4])
        changed = result["changed_pairs"][0]
        self.assertEqual((changed["old_page"], changed["new_page"]), (1, 2))
        self.assertEqual([h["text"] for h in changed["removed"]], ["10k", "COMMON", "SIGNAL"])
        self.assertEqual([h["text"] for h in changed["added"]], ["4.7k", "NO STUFF"])
        with Investigator(old_db) as old, Investigator(new_db) as new:
            self.assertTrue(all(old.citation_valid(h) for h in changed["removed"]))
            self.assertTrue(all(new.citation_valid(h) for h in changed["added"]))
        for crop in changed["crops"].values():
            self.assertTrue(Path(crop["output"]).is_file())
        self.assertEqual(hashes, (fingerprint(a), fingerprint(b)))
        short = compact_comparison(result, max_changes=1)
        self.assertEqual(short["changed_pairs"][0]["omitted_removed"], 2)
        explicit = compare_indexes(old_db, new_db, pairs=[(2, 3)])
        self.assertEqual(explicit["unchanged_text_pairs"], [{"old_page": 2, "new_page": 3}])
        with self.assertRaises(ValueError):
            compare_indexes(old_db, new_db, pairs=[(1, 2), (1, 3)])
        with a.open("ab") as f:
            f.write(b"\n%changed\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            compare_indexes(old_db, new_db)

    def test_compare_cli_saves_all_changes_before_display_limits(self):
        _, _, old_db, new_db = self.pair_fixture()
        output = self.root / "diff.json"
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["--db", str(old_db), "compare", "--other-db", str(new_db),
                         "--pair", "1", "2", "--max-changes", "1", "--output", str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(stdout.getvalue())["changed_pairs"][0]["removed"]), 1)
        self.assertEqual(len(json.loads(output.read_text())["changed_pairs"][0]["removed"]), 3)

    def test_labelled_title_block_overrides_large_revision_text(self):
        databases = []
        for name, title in [("outlined-old", "BOARD-REV-OLD"), ("outlined-new", "BOARD-REV-NEW")]:
            pdf, db = self.root / f"{name}.pdf", self.root / f"{name}.sqlite"
            with fitz.open() as doc:
                page = doc.new_page(width=600, height=400)
                page.insert_text((40, 40), title, fontsize=24)
                page.insert_text((350, 350), "PAGE DETAIL", fontsize=7)
                page.insert_text((410, 350), "POWER SEQUENCE", fontsize=7)
                page.insert_text((350, 380), "D", fontsize=7)
                doc.save(pdf)
            index_pdf(pdf, db)
            databases.append(db)
        result = compare_indexes(*databases)
        self.assertEqual(result["unpaired_old_pages"], [])
        self.assertEqual(result["changed_pairs"][0]["old_title"], "POWER SEQUENCE")
        candidate = result["changed_pairs"][0]["title_candidates"]["old"]
        with Investigator(databases[0]) as inv:
            self.assertTrue(inv.citation_valid(candidate["citation"]))


if __name__ == "__main__":
    unittest.main()
