"""Cross-board pin review candidates based on text geometry, never wire tracing."""
import math
from pathlib import Path
import re

from .core import Investigator, union


def _scope(meta, bbox):
    box = list(bbox) if bbox is not None else [0, 0, meta["width"], meta["height"]]
    if (len(box) != 4 or not all(math.isfinite(v) for v in box)
            or not 0 <= box[0] < box[2] <= meta["width"]
            or not 0 <= box[1] < box[3] <= meta["height"]):
        raise ValueError("Connector bbox must be a finite positive rectangle inside its page")
    return box


def _inside(inner, outer):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _rows(inv, page):
    return [inv.evidence(r) for r in inv.c.execute("SELECT * FROM evidence WHERE page=? ORDER BY id", (page,))]


def _endpoint(inv, meta, scope, lines, pin, max_candidates, max_occurrences, net_side):
    occurrences = []
    for line in lines:
        # Match word geometry rather than substring A1 inside A13/OA13.
        for word in line["words"]:
            if word["text"].casefold() == pin.casefold() and _inside(word["bbox"], scope):
                occurrences.append({"word_bbox": word["bbox"], "citation": line["citation"],
                                    "direction": line["direction"]})
    candidates, other_text = [], []
    for line in lines:
        names = list(dict.fromkeys(e["text"] for e in line["entities"]
                                  if e["kind"] == "net" and e["text"].casefold() != pin.casefold()))
        if line["text"].strip().upper() == "GND" and "GND" not in names:
            names.append("GND")
        other_labels = [e for e in line["entities"] if e["kind"] != "net"]
        if not (names or other_labels) or not _inside(line["bbox"], scope):
            continue
        distances = []
        aligned_ids = []
        for occurrence in occurrences:
            # Rotated labels are retained as pin hits, but need visual tracing.
            if occurrence["direction"] != [1.0, 0.0] or line["direction"] != [1.0, 0.0]:
                continue
            b, p = line["bbox"], occurrence["word_bbox"]
            if net_side == "left" and b[0] > p[0] or net_side == "right" and b[2] < p[2]:
                continue
            dy = abs((b[1]+b[3]-p[1]-p[3])/2)
            tolerance = min(2.5, max(1.5, (p[3]-p[1])*0.35))
            if dy <= tolerance:
                dx = max(0, p[0]-b[2], b[0]-p[2])
                distances.append((dx, dy))
                aligned_ids.append(occurrence["citation"]["evidence_id"])
        if distances:
            dx, dy = min(distances)
            item = {"net_labels": names, "citation": line["citation"],
                               "horizontal_gap_points": round(dx, 3), "row_offset_points": round(dy, 3),
                               "aligned_pin_evidence_ids": sorted(set(aligned_ids)),
                               "status": "unreviewed_spatial_candidate"}
            if names:
                candidates.append(item)
            if other_labels:
                other_text.append({**item, "classified_labels": other_labels})
    candidates.sort(key=lambda c: (c["horizontal_gap_points"], c["row_offset_points"], c["citation"]["evidence_id"]))
    other_text.sort(key=lambda c: (c["horizontal_gap_points"], c["row_offset_points"], c["citation"]["evidence_id"]))
    visible = candidates[:max_candidates]
    shown = occurrences[:max_occurrences]
    boxes = [o["word_bbox"] for o in shown] + [c["citation"]["bbox"] for c in visible]
    crop_bbox = None
    if boxes:
        b = union(boxes)
        crop_bbox = [max(0, b[0]-12), max(0, b[1]-12), min(meta["width"], b[2]+12), min(meta["height"], b[3]+12)]
    return {"pin": pin, "pin_occurrences": shown, "total_pin_occurrences": len(occurrences),
            "omitted_pin_occurrences": max(0, len(occurrences)-max_occurrences),
            "net_candidates": visible, "total_net_candidates": len(candidates),
            "omitted_net_candidates": max(0, len(candidates)-max_candidates), "crop_bbox": crop_bbox,
            "other_text_candidates": other_text[:max_candidates], "total_other_text_candidates": len(other_text),
            "omitted_other_text_candidates": max(0, len(other_text)-max_candidates),
            "status": "unreviewed_candidates" if occurrences else "pin_text_not_found_in_scope",
            "net_side_filter": net_side, "assembly": "unknown", "direction": "unknown", "voltage_domain": "unknown"}


def pinmap(old_db, other_db, left_page, right_page, left_connector, right_connector, pins=None,
           left_bbox=None, right_bbox=None, max_candidates=3, max_occurrences=6, crops_dir=None,
           left_net_side="either", right_net_side="either", pin_pairs=None):
    mappings = [(p, p) for p in (pins or [])] + [tuple(p) for p in (pin_pairs or [])]
    if (not 1 <= len(mappings) <= 32 or any(len(pair) != 2 for pair in mappings)
            or any(not re.fullmatch(r"[A-Za-z0-9_+-]{1,24}", p) for pair in mappings for p in pair)):
        raise ValueError("Provide 1..32 pin mappings with literal pin labels of 1..24 letters/digits/_/+/-")
    if any(len({pair[i].casefold() for pair in mappings}) != len(mappings) for i in (0, 1)):
        raise ValueError("Pin mappings must be one-to-one; duplicate endpoint labels are not allowed")
    if not 1 <= max_candidates <= 20 or not 1 <= max_occurrences <= 20:
        raise ValueError("Candidate and occurrence limits must be 1..20")
    if any(not name.strip() or len(name) > 120 for name in (left_connector, right_connector)):
        raise ValueError("Connector labels must be nonempty and at most 120 characters")
    if any(side not in {"left", "right", "either"} for side in (left_net_side, right_net_side)):
        raise ValueError("Net side must be left, right or either")
    with Investigator(old_db) as left, Investigator(other_db) as right:
        documents, contexts = {}, {}
        for side, inv, page, connector, bbox in [
                ("left", left, left_page, left_connector, left_bbox),
                ("right", right, right_page, right_connector, right_bbox)]:
            inv.verify_source()
            meta = inv.c.execute("SELECT * FROM pages WHERE page=?", (page,)).fetchone()
            if meta is None:
                raise ValueError(f"{side} page does not exist (pages are 1-based)")
            meta = dict(meta)
            scope = _scope(meta, bbox)
            documents[side] = {"source": inv.doc["path"], "sha256": inv.doc["sha256"], "page": page,
                               "connector_label": connector, "connector_label_source": "user_supplied_unverified",
                               "bbox": scope, "scope": "user_bbox" if bbox is not None else "whole_page_unscoped"}
            contexts[side] = inv, meta, scope, _rows(inv, page)
        rows = []
        for left_pin, right_pin in mappings:
            row = {"pin": left_pin, "left_pin": left_pin, "right_pin": right_pin,
                   "mapping_source": "user_requested", "connection_status": "requires_visual_review"}
            for side, pin in (("left", left_pin), ("right", right_pin)):
                inv, meta, scope, lines = contexts[side]
                endpoint = _endpoint(inv, meta, scope, lines, pin, max_candidates, max_occurrences,
                                     left_net_side if side == "left" else right_net_side)
                if crops_dir and endpoint["crop_bbox"]:
                    endpoint["crop"] = inv.crop(documents[side]["page"], Path(crops_dir) / f"{side}-{pin}.png",
                                                bbox=endpoint["crop_bbox"], dpi=200)
                row[side] = endpoint
            rows.append(row)
        return {"version": 2, "documents": documents, "rows": rows, "claims": [],
                "limitations": ["Connector names are user labels, not verified RefDes associations.",
                                "Same-row text is only a spatial candidate; no wire, junction or component tracing.",
                                "Nearby labels, matching pin numbers and equal net names do not prove connectivity or compatibility.",
                                "Assembly, direction, firmware pinmux and voltage domains require separate evidence.",
                                "Use connector bboxes to reduce other-connector hits; inspect crops and widen for endpoints.",
                                "Net-side filters describe drawing positions, not electrical signal direction.",
                                "Rotated pin text is retained, but same-row net suggestions are not generated for it.",
                                "Explicit pin mappings are caller supplied, not verified mating relationships.",
                                "Text categories are heuristics; original text/citations are retained for review."]}


def markdown_table(result):
    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    lines = ["# Cross-board pin review candidates", "", "Connection status: requires visual review.", ""]
    for side in ("left", "right"):
        d = result["documents"][side]
        lines += [f"{side}: {escape(d['connector_label'])} (user label), physical p{d['page']}, "
                  f"SHA-256 `{d['sha256']}`, scope `{d['scope']}`.", ""]
    lines += ["| Left pin | Right pin | Left label candidates | Right label candidates | Status |",
              "| --- | --- | --- | --- | --- |"]
    for row in result["rows"]:
        values = []
        for side in ("left", "right"):
            e = row[side]
            text = "; ".join(f"{', '.join(c['net_labels'])} (E{c['citation']['evidence_id']})"
                             for c in e["net_candidates"]) or e["status"]
            if e["omitted_net_candidates"]:
                text += f"; {e['omitted_net_candidates']} more candidates"
            values.append(escape(text))
        lines.append(f"| {escape(row['left']['pin'])} | {escape(row['right']['pin'])} | {values[0]} | {values[1]} | unreviewed |")
    lines += ["", "## Other nearby text (not used as net labels)", ""]
    for row in result["rows"]:
        for side in ("left", "right"):
            e = row[side]
            for candidate in e["other_text_candidates"]:
                labels = ", ".join(f"{label['text']} [{label['kind']}]" for label in candidate["classified_labels"])
                lines.append(f"- {side} {escape(e['pin'])}: {escape(labels)} (E{candidate['citation']['evidence_id']})")
            if e["omitted_other_text_candidates"]:
                lines.append(f"- {side} {escape(e['pin'])}: {e['omitted_other_text_candidates']} additional text candidates omitted")
    lines += ["", "The accompanying JSON contains original citations, word geometry and crop boxes.", ""]
    lines.extend(f"- {limit}" for limit in result["limitations"])
    return "\n".join(lines) + "\n"
