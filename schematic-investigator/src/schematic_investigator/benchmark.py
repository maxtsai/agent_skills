"""Benchmark retrieval and optional agent reports without claiming to grade reasoning."""
import json
from pathlib import Path
import pymupdf as fitz
from .core import Investigator


def overlap(a, b):
    a, b = fitz.Rect(a), fitz.Rect(b)
    intersection = (a & b).get_area()
    return intersection / (a.get_area() + b.get_area() - intersection) if intersection else 0.0


def evaluate(inv, config, reports=None):
    if config.get("version") != 1 or not config.get("cases"):
        raise ValueError("Benchmark needs version 1 and nonempty cases")
    inv.verify_source()
    if config.get("source_sha256") and config["source_sha256"] != inv.doc["sha256"]:
        raise ValueError("Benchmark source fingerprint mismatch")
    reports = {} if reports is None else reports
    if not isinstance(reports, dict):
        raise ValueError("Reports must be an object keyed by case id")
    results = []
    ids = set()
    for case in config["cases"]:
        cid = case["id"]
        if cid in ids:
            raise ValueError("Duplicate benchmark case id")
        ids.add(cid)
        result = inv.search(case["query"], expansions=case.get("expansions", []), top=5,
                            radius=case.get("radius", 100))
        pages = {p["page"] for p in result["pages"]}
        hits = [h for p in result["pages"] for h in p["hits"]]
        # Region evidence includes nearby entities, but never treats them as connected.
        evidence = {h["id"]: h for p in result["pages"] for r in p["regions"] for h in r["evidence"]}
        evidence.update({h["id"]: h for h in hits})
        entities = {e["text"].upper() for h in evidence.values() for e in h["entities"]}
        variants = [case["expected"], *case.get("alternatives", [])]
        scores = []
        for expected in variants:
            targets = set(expected.get("pages", []))
            if not targets:
                raise ValueError("Each expected/alternative result requires pages")
            recall = len(pages & targets) / len(targets)
            entity_targets = {e.upper() for key in ("components", "nets") for e in expected.get(key, [])}
            entity_recall = len(entities & entity_targets) / len(entity_targets) if entity_targets else None
            regions = expected.get("regions", [])
            localized = 0
            for target in regions:
                box = fitz.Rect(target["bbox"])
                if box.is_empty or box.is_infinite:
                    raise ValueError("Expected region must have a finite positive rectangle")
                for p in result["pages"]:
                    if p["page"] != target["page"]:
                        continue
                    if any(overlap(r["bbox"], target["bbox"]) >= case.get("region_iou", 0.25)
                           for r in p["regions"]):
                        localized += 1
                        break
            scores.append({"recall_at_5": recall, "entity_recall": entity_recall,
                           "region_accuracy": localized / len(regions) if regions else None})
        # A complete alternative is selected, never mixing pieces from alternatives.
        best_index, best = max(enumerate(scores), key=lambda pair: (
            min(v for v in pair[1].values() if v is not None),
            sum(v for v in pair[1].values() if v is not None)))
        report = reports.get(cid)
        citations = [h["citation"] for h in evidence.values()]
        if report is not None:
            if not isinstance(report, dict) or not isinstance(report.get("claims"), list) or not isinstance(report.get("citations"), list):
                raise ValueError("Each report requires claims and citations lists")
            for claim in report["claims"]:
                if (not isinstance(claim, dict) or not isinstance(claim.get("text"), str)
                    or claim.get("kind") not in {"observation", "connectivity"}
                    or claim.get("status") not in {"confirmed", "hypothesis"}
                    or not isinstance(claim.get("citations"), list)):
                    raise ValueError("Claim requires text, kind, status, and citations")
                citations += claim["citations"]
            citations += report.get("citations", [])
        valid = sum(inv.citation_valid(c) for c in citations)
        # Also resolve citation text against the original PDF, not just cached rows.
        source_valid = 0
        with fitz.open(inv.doc["path"]) as pdf:
            for citation in citations:
                if not inv.citation_valid(citation):
                    continue
                p = pdf[citation["page"]-1]
                words = p.get_text("words", clip=fitz.Rect(citation["bbox"]) + (-1, -1, 1, 1))
                actual = "".join(w[4] for w in words).replace(" ", "").casefold()
                expected_text = "".join(citation["text"].split()).casefold()
                if expected_text and expected_text in actual:
                    source_valid += 1
        claims = report.get("claims", []) if report is not None else None
        forbidden = case.get("forbidden_unsupported_conclusions", [])
        violations = []
        if claims is not None:
            for claim in claims:
                if claim.get("status") != "confirmed":
                    continue
                forbidden_match = any(s.casefold() in claim.get("text", "").casefold() for s in forbidden)
                # This MVP verifies text observations; it cannot certify topology.
                connectivity = claim.get("kind") == "connectivity"
                cited = claim.get("citations", [])
                unsupported = connectivity or not cited or not all(inv.citation_valid(c) for c in cited)
                if forbidden_match or unsupported:
                    violations.append(claim)
        item = {"id": cid, **best, "accepted_variant": best_index,
                "citation_validity": min(valid, source_valid) / len(citations) if citations else None,
                "citations_checked": len(citations), "claim_review": "provided" if report is not None else "not_provided",
                "unsupported_confirmed_claims": len(violations) if claims is not None else None,
                "unsupported_connectivity_claims": sum(c.get("kind") == "connectivity" for c in violations) if claims is not None else None,
                "violations": violations, "retrieved_pages": sorted(pages)}
        thresholds = {"recall_at_5": 1.0, "entity_recall": 1.0, "region_accuracy": 1.0,
                      "citation_validity": 1.0, **config.get("thresholds", {})}
        item["passed"] = all(item[k] is None or item[k] >= v for k, v in thresholds.items()) and not violations
        if config.get("require_reports") and report is None:
            item["passed"] = False
        results.append(item)
    metrics = {}
    for key in ("recall_at_5", "entity_recall", "region_accuracy", "citation_validity",
                "unsupported_confirmed_claims", "unsupported_connectivity_claims"):
        values = [r[key] for r in results if r[key] is not None]
        metrics[key] = sum(values) / len(values) if values else None
    return {"version": 1, "sha256": inv.doc["sha256"], "metrics": metrics,
            "passed": all(r["passed"] for r in results), "cases": results,
            "limitations": "Claim checks require structured reports and declared claim kinds. Semantic truth and unstructured prose require human review; connectivity claims are never auto-certified."}


def run_benchmark(db, path, report_path=None):
    config = json.loads(Path(path).read_text())
    reports = json.loads(Path(report_path).read_text()) if report_path else None
    with Investigator(db) as inv:
        return evaluate(inv, config, reports)
