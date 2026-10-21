from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from collections import defaultdict
from contextlib import closing
from difflib import SequenceMatcher

import pymupdf as fitz
from .classification import classify_text

SCHEMA_VERSION = 1
REFDES = re.compile(r"(?:U|R|C|L|D|Q|J|P|TP|FB|RN|SW|Y|X|CN)\d+[A-Z]?", re.I)
TOKEN = re.compile(r"[A-Za-z0-9_+./#:-]+")
STOP = set("the a an is are of on to for from with why does do not working debug check and or in at my this fails failure".split())


def fingerprint(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def union(boxes):
    boxes = list(boxes)
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def candidates(text):
    return classify_text(text)


def connect(db):
    c = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    version = c.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        c.close()
        raise ValueError("Unsupported or missing index; run index first")
    return c


def index_pdf(pdf, db):
    pdf, db = Path(pdf).resolve(), Path(db).resolve()
    if pdf == db:
        raise ValueError("Index path must differ from source PDF")
    db.parent.mkdir(parents=True, exist_ok=True)
    digest = fingerprint(pdf)
    fd, temp = tempfile.mkstemp(dir=db.parent, suffix=".sqlite")
    os.close(fd)
    try:
        with fitz.open(pdf) as doc, closing(sqlite3.connect(temp)) as c, c:
            if doc.needs_pass:
                raise ValueError("Encrypted PDF requires an unlocked local copy")
            c.executescript('''
                PRAGMA user_version=1;
                CREATE TABLE document(path TEXT, sha256 TEXT, metadata TEXT);
                CREATE TABLE pages(page INTEGER PRIMARY KEY, label TEXT, width REAL, height REAL,
                  rotation INTEGER, title TEXT);
                CREATE TABLE evidence(id INTEGER PRIMARY KEY, page INTEGER, text TEXT, bbox TEXT,
                  direction TEXT, spans TEXT, words TEXT, entities TEXT);
                CREATE INDEX evidence_page ON evidence(page);
                CREATE VIRTUAL TABLE text_fts USING fts5(text, content='evidence', content_rowid='id');
            ''')
            c.execute("INSERT INTO document VALUES(?,?,?)", (str(pdf), digest, json.dumps(doc.metadata)))
            count = 0
            for number, page in enumerate(doc, 1):
                lines = []
                words = page.get_text("words")
                word_lines = defaultdict(list)
                for w in words:
                    word_lines[(w[5], w[6])].append({"text": w[4], "bbox": list(w[:4])})
                for block in page.get_text("dict")["blocks"]:
                    for line_number, line in enumerate(block.get("lines", [])):
                        spans = line["spans"]
                        text = "".join(s["text"] for s in spans).strip()
                        if not text:
                            continue
                        # The PDF's line grouping joins font/style-split spans. Word boxes
                        # are retained separately for precise identifier crops.
                        bbox = list(line["bbox"])
                        local_words = word_lines[(block["number"], line_number)]
                        lines.append((text, bbox, line["dir"], spans, local_words))
                # Titles are candidates: prefer large text, then lower/right title blocks.
                title_lines = sorted(lines, key=lambda x: (max(s["size"] for s in x[3]), x[1][1]), reverse=True)
                largest = max((s["size"] for x in lines for s in x[3]), default=0)
                title = " | ".join(x[0] for x in title_lines[:3]
                                   if max(s["size"] for s in x[3]) >= largest - 0.5)
                c.execute("INSERT INTO pages VALUES(?,?,?,?,?,?)", (number, page.get_label(),
                          page.cropbox.width, page.cropbox.height, page.rotation, title))
                for text, bbox, direction, spans, local_words in lines:
                    cur = c.execute("INSERT INTO evidence(page,text,bbox,direction,spans,words,entities) VALUES(?,?,?,?,?,?,?)",
                        (number, text, json.dumps(bbox), json.dumps(direction),
                         json.dumps([{k: s[k] for k in ("text", "bbox", "size", "font", "origin")} for s in spans]),
                         json.dumps(local_words), json.dumps(candidates(text))))
                    c.execute("INSERT INTO text_fts(rowid,text) VALUES(?,?)", (cur.lastrowid, text))
                    count += 1
            if fingerprint(pdf) != digest:
                raise ValueError("Source changed during indexing")
            c.commit()
            page_count = len(doc)
        os.replace(temp, db)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"database": str(db), "source": str(pdf), "sha256": digest,
            "pages": page_count, "evidence_count": count}


class Investigator:
    def __init__(self, db):
        if not Path(db).is_file():
            raise ValueError("Index not found; run index first")
        self.c = connect(db)
        self.doc = dict(self.c.execute("SELECT * FROM document").fetchone())

    def close(self):
        self.c.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def verify_source(self):
        if fingerprint(self.doc["path"]) != self.doc["sha256"]:
            raise ValueError("Source PDF changed; rebuild the index")

    def evidence(self, row):
        item = dict(row)
        for k in ("bbox", "direction", "spans", "words", "entities"):
            item[k] = json.loads(item[k])
        # Apply current text rules to old indexes too; stored evidence is untouched.
        item["entities"] = candidates(item["text"])
        item["citation"] = {"sha256": self.doc["sha256"], "evidence_id": item["id"],
                            "page": item["page"], "bbox": item["bbox"], "text": item["text"]}
        return item

    def search(self, query, mode="auto", expansions=(), top=5, radius=100, details=False,
               pages=None, include_regions=True):
        self.verify_source()
        if not query.strip() or len(query) > 512 or not 1 <= top <= 100 or not 0 < radius <= 2000:
            raise ValueError("Query length, top count, or radius outside supported range")
        if mode not in {"auto", "exact", "regex", "wildcard", "fuzzy", "fts"}:
            raise ValueError("Unknown search mode")
        if len(expansions) > 32 or any(not x.strip() or len(x) > 512 for x in expansions):
            raise ValueError("Use at most 32 nonempty expansions of at most 512 characters")
        selected = sorted(set(pages)) if pages is not None else None
        if selected is not None:
            available = {r[0] for r in self.c.execute("SELECT page FROM pages")}
            if not selected or any(p not in available for p in selected):
                raise ValueError("Requested page does not exist (pages are 1-based)")
        queries = [query, *expansions]
        if mode == "auto":
            terms = list(dict.fromkeys(t for q in queries for t in TOKEN.findall(q) if t.lower() not in STOP))
        else:
            terms = queries
        patterns = [re.compile(t, re.I) for t in terms] if mode == "regex" else []
        # FTS narrows ordinary retrieval; punctuation-bearing labels also use
        # literal matching so FTS tokenizer semantics cannot erase identifiers.
        fts_ids = set()
        fts_by_term = {}
        if mode in {"auto", "fts"} and terms:
            expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
            fts_ids = {r[0] for r in self.c.execute("SELECT rowid FROM text_fts WHERE text_fts MATCH ?", (expression,))}
            if mode == "fts":
                for term in terms:
                    phrase = '"' + term.replace('"', '""') + '"'
                    fts_by_term[term] = {r[0] for r in self.c.execute(
                        "SELECT rowid FROM text_fts WHERE text_fts MATCH ?", (phrase,))}
        hits = []
        sql = "SELECT * FROM evidence"
        if selected is not None:
            sql += " WHERE page IN (" + ",".join("?" for _ in selected) + ")"
        for row in self.c.execute(sql, selected or ()):
            text = row["text"]
            tokens = TOKEN.findall(text)
            matched, quality = [], 0.0
            for i, term in enumerate(terms):
                exact = re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.I)
                score = 0.0
                if mode in {"auto", "exact"} and exact:
                    score = 1.0
                elif mode == "auto" and row["id"] in fts_ids and term.casefold() in {
                    part.casefold() for token in tokens for part in re.split(r"[_./#:+-]", token)
                }:
                    score = 0.9
                elif mode == "regex" and patterns[i].search(text[:8192]):
                    score = 0.9
                elif mode == "wildcard" and any(fnmatch.fnmatchcase(t.upper(), term.upper()) for t in tokens):
                    score = 0.9
                elif mode == "fts" and row["id"] in fts_by_term[term]:
                    score = 0.85
                elif mode in {"auto", "fuzzy"} and len(term) >= 3:
                    ratio = max((SequenceMatcher(None, term.upper(), t.upper()).ratio() for t in tokens), default=0)
                    if ratio >= 0.8:
                        score = ratio * 0.7
                if score:
                    matched.append(term)
                    quality += score
            if matched:
                hit = self.evidence(row)
                hit.update(matched_terms=matched, score=round(quality, 4))
                hits.append(hit)
        grouped = defaultdict(list)
        for hit in hits:
            grouped[hit["page"]].append(hit)
        ranked_pages = []
        for page, items in grouped.items():
            coverage = len({t.lower() for h in items for t in h["matched_terms"]})
            meta = dict(self.c.execute("SELECT * FROM pages WHERE page=?", (page,)).fetchone())
            # Coverage dominates repeated occurrence counts on a busy sheet.
            score = coverage * 10 + sum(sorted((h["score"] for h in items), reverse=True)[:5])
            ranked_pages.append({**meta, "score": round(score, 4),
                                 "matched_terms": sorted({t for h in items for t in h["matched_terms"]}),
                                 "hits": sorted(items, key=lambda h: (-h["score"], h["id"])), "regions": []})
        ranked_pages.sort(key=lambda p: (-p["score"], p["page"]))
        pages = ranked_pages[:top]
        for result_page in pages if include_regions else []:
            page, items, meta = result_page["page"], result_page["hits"], result_page
            regions = result_page["regions"]
            region_candidates = []
            for hit in items:
                b = hit["bbox"]
                box = [max(0, b[0]-radius), max(0, b[1]-radius),
                       min(meta["width"], b[2]+radius), min(meta["height"], b[3]+radius)]
                local = [h for h in items if h["bbox"][0] < box[2] and h["bbox"][2] > box[0]
                         and h["bbox"][1] < box[3] and h["bbox"][3] > box[1]]
                local_coverage = len({t.casefold() for h in local for t in h["matched_terms"]})
                local_score = local_coverage * 10 + sum(sorted((h["score"] for h in local), reverse=True)[:5])
                region_candidates.append((local_score, hit, box))
            for local_score, hit, box in sorted(region_candidates, key=lambda r: (-r[0], -r[1]["score"], r[1]["id"])):
                if any(fitz.Rect(r["bbox"]).contains(fitz.Rect(hit["bbox"])) for r in regions):
                    continue
                nearby = self.neighborhood(page, box)
                regions.append({"page": page, "bbox": box, "score": round(local_score, 4),
                                "anchor": hit["citation"], "evidence": nearby,
                                "interpretation": "Spatial neighborhood only; connectivity unconfirmed"})
                if len(regions) >= 5:
                    break
        if not details:
            for p in pages:
                for hit in p["hits"]:
                    hit.pop("spans", None)
                    hit.pop("words", None)
                for region in p["regions"]:
                    for hit in region["evidence"]:
                        hit.pop("spans", None)
                        hit.pop("words", None)
        return {"query": query, "expansions": list(expansions), "mode": mode, "source": self.doc["path"],
                "sha256": self.doc["sha256"], "coordinates": "unrotated PDF points, top-left origin; pages are 1-based",
                "total_matching_pages": len(ranked_pages), "omitted_pages": max(0, len(ranked_pages)-top),
                "page_filter": selected, "regions_included": include_regions,
                "pages": pages, "claims": []}

    def inspect_evidence(self, evidence_id, radius=40, max_neighbors=20, details=False):
        self.verify_source()
        if not 0 < radius <= 2000 or not 1 <= max_neighbors <= 200:
            raise ValueError("Radius must be 0..2000 (exclusive zero); max-neighbors must be 1..200")
        row = self.c.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if row is None:
            raise ValueError("Evidence ID not found in this index")
        item = self.evidence(row)
        page = dict(self.c.execute("SELECT * FROM pages WHERE page=?", (item["page"],)).fetchone())
        b = item["bbox"]
        box = [max(0, b[0]-radius), max(0, b[1]-radius),
               min(page["width"], b[2]+radius), min(page["height"], b[3]+radius)]
        neighbors = [h for h in self.neighborhood(item["page"], box) if h["id"] != evidence_id]
        neighbors.sort(key=lambda h: (sum((h["bbox"][i]+h["bbox"][i+2]-b[i]-b[i+2])**2
                                         for i in (0, 1)), h["id"]))
        return {"source": self.doc["path"], "sha256": self.doc["sha256"], "page": page,
                "bbox": box, "evidence": item if details else item["citation"],
                "neighbors": [h if details else h["citation"] for h in neighbors[:max_neighbors]],
                "total_neighbors": len(neighbors), "omitted_neighbors": max(0, len(neighbors)-max_neighbors),
                "interpretation": "Spatial neighborhood only; connectivity unconfirmed"}

    def neighborhood(self, page, bbox):
        rect = fitz.Rect(bbox)
        return [self.evidence(r) for r in self.c.execute("SELECT * FROM evidence WHERE page=?", (page,))
                if rect.intersects(fitz.Rect(json.loads(r["bbox"])))]

    def crop(self, page, output, refdes=None, bbox=None, margin=100, dpi=200):
        if not 36 <= dpi <= 1200 or not 0 <= margin <= 2000:
            raise ValueError("DPI must be 36..1200; margin 0..2000")
        meta = self.c.execute("SELECT * FROM pages WHERE page=?", (page,)).fetchone()
        if not meta:
            raise ValueError("Page does not exist (pages are 1-based)")
        self.verify_source()
        anchors = []
        if refdes:
            result = self.search(refdes, "exact", top=1, details=True, pages=[page], include_regions=False)
            anchors = [h for p in result["pages"] if p["page"] == page for h in p["hits"]]
            if not anchors:
                raise ValueError("RefDes not found on requested page")
            boxes = [w["bbox"] for h in anchors for w in h["words"] if w["text"].upper() == refdes.upper()]
            box = union(boxes or [h["bbox"] for h in anchors])
            bbox = [box[0]-margin, box[1]-margin, box[2]+margin, box[3]+margin]
        elif bbox is None:
            bbox = [0, 0, meta["width"], meta["height"]]
        if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox) or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise ValueError("Invalid crop rectangle")
        clipped = fitz.Rect(bbox) & fitz.Rect(0, 0, meta["width"], meta["height"])
        if clipped.is_empty:
            raise ValueError("Crop is outside page")
        if clipped.width * clipped.height * (dpi / 72)**2 > 80_000_000:
            raise ValueError("Crop exceeds 80 megapixels; reduce DPI or region size")
        output = Path(output).resolve()
        if output == Path(self.doc["path"]).resolve() or output.suffix.lower() != ".png":
            raise ValueError("Output must be a PNG distinct from source")
        with fitz.open(self.doc["path"]) as doc:
            p = doc[page-1]
            # All API coordinates remain unrotated even for rotated PDF pages.
            p.set_rotation(0)
            pix = p.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), clip=clipped, alpha=False)
            output.parent.mkdir(parents=True, exist_ok=True)
            pix.save(output)
        return {"output": str(output), "page": page, "bbox": list(clipped), "dpi": dpi,
                "width": pix.width, "height": pix.height, "sha256": self.doc["sha256"],
                "anchors": [h["citation"] for h in anchors]}

    def citation_valid(self, citation):
        try:
            row = self.c.execute("SELECT * FROM evidence WHERE id=?", (citation["evidence_id"],)).fetchone()
            return bool(row and citation == self.evidence(row)["citation"])
        except (KeyError, TypeError):
            return False
