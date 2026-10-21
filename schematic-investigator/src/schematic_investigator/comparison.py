"""Conservative page alignment and cited text differences; no wiring inference."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from .core import Investigator


def _title(text):
    return " ".join(text.casefold().split())


def _citations(inv, page):
    return [inv.evidence(row)["citation"] for row in inv.c.execute(
        "SELECT * FROM evidence WHERE page=? ORDER BY id", (page,))]


def _alignment_title(inv, meta):
    # Some EDA exports outline their large headings: the small title-block text
    # remains searchable. Prefer an explicitly labelled footer over font size.
    lines = [{"text": row["text"], "bbox": json.loads(row["bbox"]), "row": row}
             for row in inv.c.execute("SELECT * FROM evidence WHERE page=?", (meta["page"],))]
    for anchor in lines:
        if _title(anchor["text"]) not in {"page detail", "sheet title"}:
            continue
        a = anchor["bbox"]
        if a[1] < meta["height"] * 0.5:
            continue
        labels = [line for line in lines if len(line["text"].strip()) >= 3
                  and any(c.isalpha() for c in line["text"])]
        right = [line for line in labels
                 if a[2] <= line["bbox"][0] < a[2]+meta["width"]*0.12
                 and abs((line["bbox"][1]+line["bbox"][3])-(a[1]+a[3])) <= max(4, a[3]-a[1])]
        below = [line for line in labels
                 if a[3] <= line["bbox"][1] < a[3]+2*(a[3]-a[1])
                 and abs(line["bbox"][0]-a[0]) < meta["width"]*0.015]
        if right or below:
            selected = min(right, key=lambda line: line["bbox"][0]) if right else min(
                below, key=lambda line: line["bbox"][1])
            return {"text": selected["text"], "basis": "labelled_title_block_candidate",
                    "citation": inv.evidence(selected["row"])["citation"]}
    return {"text": meta["title"], "basis": "indexed_font_size_candidate"}


def _excess(items, other):
    remaining = Counter(" ".join(h["text"].split()) for h in other)
    result = []
    for item in items:
        key = " ".join(item["text"].split())
        if remaining[key]:
            remaining[key] -= 1
        else:
            result.append(item)
    return result


def compare_indexes(old_db, new_db, pairs=None, crops_dir=None, max_pairs=5, dpi=144):
    if not 1 <= max_pairs <= 100 or not 36 <= dpi <= 300:
        raise ValueError("max-pairs must be 1..100; comparison DPI must be 36..300")
    with Investigator(old_db) as old, Investigator(new_db) as new:
        old.verify_source()
        new.verify_source()
        old_pages = {p["page"]: dict(p) for p in old.c.execute("SELECT * FROM pages")}
        new_pages = {p["page"]: dict(p) for p in new.c.execute("SELECT * FROM pages")}
        titles = [{p: _alignment_title(inv, meta) for p, meta in pages.items()}
                  for inv, pages in ((old, old_pages), (new, new_pages))]
        explicit = pairs is not None
        if explicit:
            aligned = [tuple(p) for p in pairs]
            if (any(a not in old_pages or b not in new_pages for a, b in aligned)
                    or len({a for a, _ in aligned}) != len(aligned)
                    or len({b for _, b in aligned}) != len(aligned)):
                raise ValueError("Page pairs must exist and be one-to-one")
        else:
            groups = []
            for page_titles in titles:
                group = defaultdict(list)
                for page, candidate in page_titles.items():
                    if _title(candidate["text"]):
                        group[_title(candidate["text"])].append(page)
                groups.append(group)
            aligned = sorted((numbers[0], groups[1][title][0])
                             for title, numbers in groups[0].items()
                             if len(numbers) == 1 and len(groups[1].get(title, [])) == 1)
        changes, unchanged = [], []
        for a, b in aligned:
            left, right = _citations(old, a), _citations(new, b)
            removed, added = _excess(left, right), _excess(right, left)
            if not removed and not added:
                unchanged.append({"old_page": a, "new_page": b})
                continue
            changes.append({"old_page": a, "new_page": b,
                            "old_title": titles[0][a]["text"], "new_title": titles[1][b]["text"],
                            "title_candidates": {"old": titles[0][a], "new": titles[1][b]},
                            "alignment": "explicit" if explicit else "unique_exact_title_candidate",
                            "removed": removed, "added": added,
                            "removed_count": len(removed), "added_count": len(added)})
        if crops_dir:
            directory = Path(crops_dir)
            # Render only the displayed pairs, without changing either source PDF.
            for change in changes[:max_pairs]:
                a, b = change["old_page"], change["new_page"]
                change["crops"] = {
                    "old": old.crop(a, directory / f"old-{a}-new-{b}-old.png", dpi=dpi),
                    "new": new.crop(b, directory / f"old-{a}-new-{b}-new.png", dpi=dpi)}
        return {
            "old": {"source": old.doc["path"], "sha256": old.doc["sha256"]},
            "new": {"source": new.doc["path"], "sha256": new.doc["sha256"]},
            "scope": "explicit_page_pairs" if explicit else "unique_exact_title_candidates",
            "alignment_requires_visual_review": not explicit,
            "unpaired_old_pages": [p for p in old_pages if p not in {a for a, _ in aligned}],
            "unpaired_new_pages": [p for p in new_pages if p not in {b for _, b in aligned}],
            "unchanged_text_pairs": unchanged, "changed_pairs": changes,
            "total_changed_pairs": len(changes),
            "rendered_pairs": min(max_pairs, len(changes)) if crops_dir else 0,
            "limitations": ["Text multiset differences only; whitespace is normalized.",
                            "Identical text does not establish identical wiring, placement or assembly.",
                            "Changed values and assembly states require visual association with a component.",
                            "Unpaired pages require explicit --pair; they are not proven added or deleted sheets."],
            "claims": []}


def compact_comparison(result, max_pairs=5, max_changes=20):
    if not 1 <= max_changes <= 200:
        raise ValueError("max-changes must be 1..200")
    summary = {k: v for k, v in result.items() if k != "changed_pairs"}
    summary["changed_pairs"] = []
    for change in result["changed_pairs"][:max_pairs]:
        summary["changed_pairs"].append({
            **change, "removed": change["removed"][:max_changes], "added": change["added"][:max_changes],
            "omitted_removed": max(0, change["removed_count"]-max_changes),
            "omitted_added": max(0, change["added_count"]-max_changes)})
    summary["omitted_changed_pairs"] = max(0, result["total_changed_pairs"]-max_pairs)
    return summary
