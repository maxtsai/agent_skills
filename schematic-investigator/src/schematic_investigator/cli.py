import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys

from .core import Investigator, index_pdf
from .benchmark import run_benchmark
from .comparison import compare_indexes, compact_comparison

DEFAULT_DB = ".schematic-investigator/index.sqlite"


def compact_result(result, max_hits):
    """Keep bounded, independently usable citations without repeated neighborhoods."""
    summary = {key: value for key, value in result.items() if key != "pages"}
    summary["pages"] = []
    for page in result["pages"]:
        hits = page["hits"]
        summary["pages"].append({
            "page": page["page"], "title": page.get("title"),
            "total_hits": len(hits), "omitted_hits": max(0, len(hits) - max_hits),
            "hits": [hit["citation"] for hit in hits[:max_hits]],
        })
    summary["compact"] = True
    return summary


def parser():
    p = argparse.ArgumentParser(description="Local vector schematic PDF evidence retrieval")
    p.add_argument("--db", default=DEFAULT_DB, help="Single-document SQLite index")
    sub = p.add_subparsers(dest="command", required=True)
    idx = sub.add_parser("index")
    idx.add_argument("pdf")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--mode", choices=["auto", "exact", "regex", "wildcard", "fuzzy", "fts"], default="auto")
    search.add_argument("--expand", action="append", default=[], help="Agent-provided synonym or identifier; repeatable")
    search.add_argument("--top", type=int, default=5)
    search.add_argument("--radius", type=float, default=100)
    search.add_argument("--details", action="store_true", help="Include original span and word geometry")
    locate = sub.add_parser("locate")
    locate.add_argument("refdes")
    locate.add_argument("--top", type=int, default=100)
    locate.add_argument("--details", action="store_true")
    for command in (search, locate):
        command.add_argument("--page", action="append", type=int, help="Restrict physical page; repeatable")
        command.add_argument("--compact", action="store_true", help="Print bounded citations without neighborhoods")
        command.add_argument("--max-hits", type=int, default=8, help="Hits per page in compact output (default: 8)")
        command.add_argument("--output", help="Save complete retrieval JSON before compacting stdout")
    status = sub.add_parser("status", help="Verify source fingerprint against the index")
    evidence = sub.add_parser("evidence", help="Expand one evidence ID and its spatial neighborhood")
    evidence.add_argument("id", type=int)
    evidence.add_argument("--radius", type=float, default=40)
    evidence.add_argument("--max-neighbors", type=int, default=20)
    evidence.add_argument("--details", action="store_true")
    compare = sub.add_parser("compare", help="Compare cited text on aligned pages from two indexes")
    compare.add_argument("--other-db", required=True, help="New revision index; --db is the old index")
    compare.add_argument("--pair", type=int, nargs=2, action="append", metavar=("OLD_PAGE", "NEW_PAGE"))
    compare.add_argument("--max-pairs", type=int, default=5)
    compare.add_argument("--max-changes", type=int, default=20, help="Displayed added/removed lines per pair")
    compare.add_argument("--crops-dir", help="Render old/new pages for displayed changed pairs")
    compare.add_argument("--dpi", type=int, default=144)
    compare.add_argument("--output", help="Save all differences and full citations")
    crop = sub.add_parser("crop")
    crop.add_argument("--page", type=int, required=True)
    group = crop.add_mutually_exclusive_group()
    group.add_argument("--refdes")
    group.add_argument("--bbox", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    crop.add_argument("--margin", type=float, default=100)
    crop.add_argument("--dpi", type=int, default=200)
    crop.add_argument("--output")
    bench = sub.add_parser("benchmark")
    bench.add_argument("config")
    bench.add_argument("--reports", help="JSON object keyed by benchmark case id")
    bench.add_argument("--output")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "index":
            result = index_pdf(args.pdf, args.db)
        elif args.command == "compare":
            if not 1 <= args.max_changes <= 200:
                raise ValueError("max-changes must be 1..200")
            result = compare_indexes(args.db, args.other_db, args.pair, args.crops_dir, args.max_pairs, args.dpi)
            if args.output:
                Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            result = compact_comparison(result, args.max_pairs, args.max_changes)
        elif args.command == "benchmark":
            result = run_benchmark(args.db, args.config, args.reports)
            if args.output:
                Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
        else:
            with Investigator(args.db) as inv:
                if args.command == "search":
                    if args.max_hits < 1:
                        raise ValueError("--max-hits must be positive")
                    result = inv.search(args.query, args.mode, args.expand, args.top, args.radius, args.details,
                                        pages=args.page, include_regions=not args.compact or bool(args.output))
                elif args.command == "locate":
                    if args.max_hits < 1:
                        raise ValueError("--max-hits must be positive")
                    result = inv.search(args.refdes, mode="exact", top=args.top, details=args.details,
                                        pages=args.page, include_regions=not args.compact or bool(args.output))
                elif args.command == "status":
                    inv.verify_source()
                    result = {"source": inv.doc["path"], "sha256": inv.doc["sha256"], "verified": True,
                              "pages": inv.c.execute("SELECT count(*) FROM pages").fetchone()[0]}
                elif args.command == "evidence":
                    result = inv.inspect_evidence(args.id, args.radius, args.max_neighbors, args.details)
                else:
                    result = inv.crop(args.page, args.output or f".schematic-investigator/crops/page-{args.page}.png",
                                      args.refdes, args.bbox, args.margin, args.dpi)
        if args.command in ("search", "locate"):
            if args.max_hits < 1:
                raise ValueError("--max-hits must be positive")
            if args.output:
                Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            if args.compact:
                result = compact_result(result, args.max_hits)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if args.command == "benchmark" and not result["passed"] else 0
    except (OSError, ValueError, sqlite3.Error, RuntimeError, KeyError, TypeError, re.error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
