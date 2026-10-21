# schematic-investigator

A local-first Python CLI and Agent Skill for finding evidence in multipage vector EDA schematic PDFs. It indexes text and geometry with PyMuPDF, retrieves through SQLite FTS5 and literal/pattern/fuzzy matching, ranks pages and spatial regions, and renders high-resolution PNG crops. It makes no electrical connectivity claims.

## Setup

Python 3.10+ and SQLite with FTS5 are required.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate
schematic-investigator --help
```

Runtime dependency: PyMuPDF. Tests use standard-library unittest. Package installation may download dependencies; document processing makes no network requests. Review PyMuPDF licensing for your distribution/use case.

The reusable skill is [skills/schematic-investigator/SKILL.md](skills/schematic-investigator/SKILL.md). Copy that directory into your agent's skill directory after installing the CLI in its environment. Keeping the skill in the repository avoids modifying global agent settings during development.

## Commands

```bash
schematic-investigator index board.pdf
schematic-investigator search "BMC I2C"
schematic-investigator locate U23
schematic-investigator crop --page 37 --refdes U23
schematic-investigator benchmark tests.json
```

The default database is `.schematic-investigator/index.sqlite` in the current directory. An index contains one PDF; indexing another replaces it atomically. Use the global option **before** the subcommand to select another database:

```bash
schematic-investigator --db local/board.sqlite index board.pdf
schematic-investigator --db local/board.sqlite search "Why does the management bus hang?" \
  --expand BMC --expand I2C --expand SDA --expand SCL
schematic-investigator search 'BMC_I2C_*' --mode wildcard
schematic-investigator search 'BMC.*SD[AL]' --mode regex
schematic-investigator search 'PCA954B' --mode fuzzy
schematic-investigator search 'BMC_I2C_SDA' --mode exact
schematic-investigator search 'BMC I2C' --mode fts
schematic-investigator search 'BMC I2C' --top 5 --radius 120
schematic-investigator crop --page 37 --bbox 100 200 600 550 --dpi 300 --output local/bus.png
```

All commands emit JSON. Success exits 0; a failed benchmark exits 1; invalid input or execution failure exits 2 with JSON on stderr. Search evidence includes `citation`, `bbox`, `direction`, candidate entities and matching terms. Add `--details` to search or locate to include original spans and word geometry; these are always preserved in SQLite. Search, locate, evidence expansion, comparison and crops verify the source SHA-256 before returning evidence. A missing or changed source is rejected; re-index after editing or moving the PDF. `status` explicitly checks the index/source pair.

Pages use **one-based physical page numbers**. Printed PDF page labels are separate. Bounding boxes are `[x0,y0,x1,y1]` in **unrotated PDF points (72/inch), top-left origin**. Crops are rendered in that orientation even for rotated pages. `crop --refdes` anchors to matching word boxes where available; multiple exact occurrences on the same page are enclosed together. Increase the margin for surrounding circuitry. PNG rendering is capped at 80 megapixels.

## Retrieval and evidence model

SQLite stores source SHA-256 and PDF metadata, page dimensions/rotation/labels/title candidates, and text-line evidence with original span and word boxes. Text spans from the same PDF line are joined; rotated writing direction is retained. Original vector drawing paths remain in the PDF and appear in crops; they are not parsed into a circuit graph. The index is an evidence locator, not a PDF archive.

Auto retrieval tokenizes the question, drops common stop words, checks exact labels/subwords via FTS5, and uses standard-library SequenceMatcher at similarity >=0.8 for remaining spelling candidates. Explicit expansions are added by the agent or user. Wildcards match whole label tokens; exact searches match bounded literals; regex searches line text with case-insensitive Python regex. FTS uses quoted phrases (no raw FTS syntax). Fuzzy similarity is a retrieval heuristic, not confidence in device identity.

Page score is `10 * distinct matched terms + sum(top five line quality scores)`. Exact hits score 1, FTS subword hits 0.9, and fuzzy hits `0.7 * similarity`. Regions are radius-expanded hit boxes clipped to page bounds, scored with the same coverage formula over local hits, with up to five per page; covered anchors are suppressed. This favors clusters of relevant labels over isolated title hits. Neighborhoods include intersecting text lines and never assert connections. Titles and entity types are heuristic candidates: net labels, part numbers, and ordinary text can be confused.

No OCR, embeddings, vector database, graph reconstruction, LLM client, or agent framework is included. Scans/image-only pages yield no searchable evidence. Text split into different PDF lines or separate drawing primitives may need wildcard/regex variants or manual visual inspection. Local Python regex is for trusted patterns; length bounds do not prevent pathological backtracking. Retrieval currently scans line candidates for pattern/fuzzy scoring and region neighborhoods; very large PDFs may be slow.

## Tests and reproducible benchmark

```bash
python -m unittest discover -s tests -v
# Create a local synthetic vector fixture and benchmark it:
PYTHONPATH=tests python -c "from fixtures import make_pdf; make_pdf('/tmp/schematic-fixture.pdf')"
schematic-investigator --db /tmp/schematic-fixture.sqlite index /tmp/schematic-fixture.pdf
schematic-investigator --db /tmp/schematic-fixture.sqlite benchmark benchmarks/synthetic.json \
  --output /tmp/schematic-benchmark.json
# Opt-in smoke test of your real local PDF:
SCHEMATIC_TEST_PDF=/path/to/board.pdf python -m unittest discover -s tests -v
```

The real PDF smoke test verifies extractability, citation resolution and cropping, not retrieval correctness. For real accuracy testing, copy [benchmarks/real-template.json](benchmarks/real-template.json), replace all example labels/pages/regions with manually reviewed ground truth, and optionally add the index's `source_sha256` at the top level to pin the revision. No real schematic is bundled with the package.

Benchmark format version 1:

- `cases`: nonempty list of unique `id`, natural-language `query`, optional `expansions`, required `expected`, optional `alternatives`, `forbidden_unsupported_conclusions`, `radius`, and `region_iou` (default 0.25).
- Each expected/alternative result contains required `pages` and optional `components`, `nets`, `regions`. A region is `{ "page": 2, "bbox": [120,80,430,360] }`. Alternatives are complete acceptable outcomes. Scoring chooses one complete variant by highest minimum metric, then highest total, rather than mixing expectations.
- `thresholds` sets minimum metrics, defaulting to 1.0 for all retrieval/citation metrics. `require_reports: true` requires agent report input per case.

Metrics are macro-averaged across applicable cases:

| Metric | Definition |
| --- | --- |
| Recall@5 | Fraction of expected physical pages among the five retrieved pages |
| Entity recall | Fraction of expected labels found among retrieved hits and region-neighborhood candidates (case-insensitive) |
| Region accuracy | Fraction of expected regions having a same-page returned region with IoU >= `region_iou` |
| Citation validity | Fraction resolving exactly to indexed text/page/box/hash, with text independently extracted from the original PDF box |
| Unsupported connectivity claims | Count per case of supplied confirmed claims declared `kind: connectivity`; all require manual review |
| Unsupported confirmed claims | Count per case with missing/invalid citations, forbidden substrings, or connectivity kind |

Unspecified region/entity metrics and missing claim review are **null**, not measured successes. Zero returned citations yields null citation validity; page/entity/region targets still fail when absent. Benchmark output includes per-case results, chosen variant and violations. Unsupported-claim aggregates are mean counts per reviewed case, not error-rate percentages. Claim checks do not establish semantic entailment or detect undeclared connectivity in free text.

Pass structured agent reports using `benchmark tests.json --reports reports.json`. Example shape (replace the citation with a complete CLI citation):

```json
{
  "bmc-bus": {
    "citations": [],
    "claims": [
      {"text": "A bus path through U23 is a hypothesis to inspect", "kind": "connectivity", "status": "hypothesis", "citations": []}
    ]
  }
}
```

Copy actual full citations from search output for confirmed observations. Citation validity proves provenance, not the truth of an electrical diagnosis. The skill requires agents to distinguish observations from hypotheses and visually inspect ambiguous circuitry. Optional external reasoning is outside this implementation and requires explicit configuration and user approval before any schematic content is sent.

## Compact retrieval and global Codex installation

Use bounded console output while keeping full evidence on disk:

```bash
schematic-investigator --db local/board.sqlite search 'POWER_GOOD' --mode exact --compact --top 3 --max-hits 8 --output evidence.json
schematic-investigator --db local/board.sqlite locate U23 --compact --top 3
```

`--compact` skips region construction and neighborhood lookups when no `--output` is requested, and keeps full citations for displayed hits. With `--output`, full region retrieval is performed for the saved file.
`omitted_hits` counts hidden hits per returned page; `omitted_pages` reports matching pages hidden by `--top`. `total_matching_pages` applies to the selected page scope.
`--output` saves the complete retrieval result before console compaction. Omit
`--compact` for the original output format. These options do not reconstruct wiring.

Install the runtime and skill into `${CODEX_HOME:-$HOME/.codex}/skills`:

```bash
bash scripts/install_global.sh
```

The installer uses a private virtual environment and installs the local package;
it may download Python dependencies. The skill resolves its runtime relative to its
own directory. Start a new Codex session to refresh skill discovery if necessary.

## Publish a standalone GitHub repository

When this directory is inside a larger repository, export an isolated copy first:

```bash
python3 scripts/export_github.py /tmp/schematic-investigator-github
cd /tmp/schematic-investigator-github
git init -b main
git add README.md pyproject.toml .gitignore src tests benchmarks skills scripts
git diff --cached --stat
git diff --cached
# Review staged code and choose the appropriate license before publishing.
git commit -m "Add schematic investigation skill with compact evidence retrieval"
# Requires GitHub CLI authentication; creates a private repository and pushes.
gh repo create schematic-investigator --private --source=. --remote=origin --push
```

The exporter uses a code-file allowlist and refuses an existing destination.
It excludes schematic PDFs, investigation reports, crops, databases and virtual environments.
After reviewing the staged files, add a suitable LICENSE if you intend to grant reuse rights.
For an existing empty GitHub repository, replace `gh repo create` with:

```bash
git remote add origin https://github.com/YOUR_ACCOUNT/schematic-investigator.git
git push -u origin main
```

## Focused retrieval and revision comparison (0.2)

Check that the original PDF still matches its index, then restrict searches to known
physical pages. `--page` may be repeated and is applied before ranking:

```bash
schematic-investigator --db local/board.sqlite status
schematic-investigator --db local/board.sqlite search 'POWER_GOOD' --mode exact --page 7 --page 12 --compact
schematic-investigator --db local/board.sqlite locate U23 --page 7 --compact
schematic-investigator --db local/board.sqlite evidence 123 --radius 40 --max-neighbors 12
```

Evidence IDs belong to a single index. `evidence` returns the original citation,
page metadata and a crop-ready neighborhood `bbox`. Nearby citations are ordered
by distance, with `omitted_neighbors` reporting truncation. Add `--details` only
when original spans/word geometry are needed. Spatial proximity does not establish
connectivity. Source verification hashes the PDF for each retrieval operation;
this adds disk I/O but detects edits even when the file size is unchanged.

Compare two existing indexes, each built from its own revision:

```bash
schematic-investigator --db local/old.sqlite compare --other-db local/new.sqlite --max-pairs 3 --max-changes 12 --output diff.json
# Explicit pairs restrict the comparison to those sheets; they override auto alignment.
schematic-investigator --db local/old.sqlite compare --other-db local/new.sqlite --pair 7 7 --pair 11 12 --crops-dir local/comparison --output focused-diff.json
```

Automatic alignment proposes pairs whose normalized titles match exactly and
occur once in each document. Labelled footer fields (`PAGE DETAIL` / `SHEET TITLE`)
are preferred when available; otherwise the indexed font-size title is used. The
selected footer title includes its own citation. Titles remain heuristic candidates:
confirm pairs visually. Duplicate, renamed and unmatched sheets appear under
`unpaired_old_pages` / `unpaired_new_pages`; use `--pair` to resolve them. They are
not automatically classified as inserted/deleted sheets.

Each paired page is compared as a multiset of extracted text lines, normalizing
whitespace and preserving occurrence counts. Added/removed lines carry full
citations including document hash, evidence ID, page and bounding box. Changes to
values, net names or assembly annotations are candidates for visual review; the
comparison does not automatically associate values with nearby components.

Console output prioritizes circuit-text candidates, reports omitted line/pair
counts and separately counts recognized frame metadata. `--show-frame` includes
all text in the displayed lists. `--output` saves all original differences plus
separate circuit/frame groups within the selected alignment scope before display limits. `--crops-dir` renders
separate old/new full-page PNGs for displayed changed pairs only (default 144 DPI,
maximum 300), with paths in JSON. Use a cited bounding box with `crop` for a tighter
view. Source PDFs are never modified by comparison rendering.

**Limits:** identical text can conceal rerouted wires, moved labels, junction
changes or swapped component values. `unchanged_text_pairs` means only unchanged
text counts. This command does not establish electrical equivalence or a root
cause; inspect old/new diagrams and verify the proposed mechanism by measurement.

## Cross-board pin review candidates (0.3)

Build a review table from two indexed connector pages:

```bash
schematic-investigator --db local/host.sqlite pinmap --other-db local/card.sqlite --left-page 12 --right-page 9 --left-connector HOST-J1 --right-connector CARD-J2 --pin A13 --pin A14 --left-net-side left --right-net-side left --output pins.json --markdown pins.md --crops-dir local/pin-crops
```

`--pin` requests the same label on both sides. Repeatable `--pin-pair LEFT RIGHT`
requests different labels, such as `--pin-pair A13 27`, or an explicitly supplied
mirrored mapping. Both forms may be mixed (1..32 one-to-one mappings; duplicate
left or right labels are rejected). Matching uses exact PDF word text: A1 does
not match A13 or OA13. Requested mappings do not establish physical mating or compatibility.
Connector labels are supplied by the caller and are not verified RefDes associations.

Use `--left-bbox X0 Y0 X1 Y1` / `--right-bbox` to restrict each connector region,
including the labels you want considered. Without boxes the scope is explicitly
`whole_page_unscoped`. `--left-net-side` / `--right-net-side` filter label positions
relative to pin text; they do not establish signal direction. Split A/B banks into
separate requests when they need different filters. Limits default to 3 net
candidates and 6 pin occurrences per endpoint; omitted counts are explicit.

The JSON preserves document hashes, physical pages, pin word boxes, citations,
matched pin evidence IDs, unknown assembly/direction/voltage fields and crop-ready
boxes. Markdown lists label candidates and evidence IDs; retain its accompanying
JSON for full provenance. Console output is a short file summary. Optional crops
are generated at 200 DPI for found pins; widen them if a wire endpoint or connector
identity is outside the suggested region. Missing text does not establish absent
pins. Rotated pin text is retained, but no horizontal net candidates are proposed.

**Every result requires visual review.** The tool does not follow wires, establish
junctions, identify pull-ups/downs, infer pinmux, or certify compatibility. Even a
single nearby label can belong to another bank or an unconnected line.

## Electrical review fixtures

Generate and index an entirely synthetic PDF:

```bash
python scripts/make_review_fixture.py /tmp/electrical-review.pdf
schematic-investigator --db /tmp/electrical-review.sqlite index /tmp/electrical-review.pdf
schematic-investigator --db /tmp/electrical-review.sqlite benchmark benchmarks/electrical-review.json --reports review-reports.json
```

Use a Python environment with PyMuPDF to run the generator. The generator refuses
existing output files. The benchmark requires reports keyed by case ID in the
existing `citations` / `claims` schema. Inspect diagrams and produce substantive
reports before checking the [reviewer criteria](skills/schematic-investigator/references/electrical-review.md).
The fixtures cover resistor endpoints, NO STUFF, crossings without junction dots,
unknown GPIO direction, separate rails, reset versus power loss, duplicate pins,
opposite-bank decoys and rotation. Automated checks validate retrieval/provenance
and specific overclaim checks; they do not establish that a model reasons correctly.

## Explicit mappings, text categories and frame metadata (0.4)

```bash
schematic-investigator --db local/host.sqlite pinmap --other-db local/card.sqlite --left-page 12 --right-page 9 --left-connector J1 --right-connector J9 --pin A1 --pin-pair A13 27 --pin-pair B48 28 --output mapped-pins.json --markdown mapped-pins.md
schematic-investigator --db local/old.sqlite compare --other-db local/new.sqlite --show-frame --output all-differences.json
```

Pinmap JSON schema is now version 2: rows have explicit `left_pin` / `right_pin`
and caller-supplied mapping status. The legacy row `pin` remains the left label;
endpoint `pin` is authoritative for each side. Each crop uses its own side's pin
label. CLI missing-pin summaries also report the requested label for that side.

Text categories are heuristic candidates with reasons. Rules separate net-class
annotations such as `50OHM_NETCLASS1`, component values such as `4.7k` / `0.1uF`,
RefDes and assembly annotations such as `NO STUFF` / `COMMON` / `DNI` from net-label
suggestions. Pinmap retains these in `other_text_candidates` and a separate
Markdown section, with independent limits and omission counts. Mixed text lines
can appear in both categories with the same original citation. Original text,
geometry and SHA-256 evidence are preserved; classification does not certify
semantics, component association, population, direction or voltage domain.
Current classification is applied when reading old indexes; no re-index is needed
solely for these text categories.

Revision comparison separates only recognizable date, revision, page-count and
document-ID patterns near the top/bottom page perimeter. Position alone never
classifies a footer component value as frame text; date-looking text inside the
circuit body remains visible. Defaults show circuit-text candidates first and
list frame-only page-pair counts separately. `--show-frame` restores all text to
console lists; `--output` always preserves raw `removed` / `added` citations plus
`circuit_removed` / `circuit_added` and `frame_removed` / `frame_added` groups.
Frame entries wrap their original citation with a heuristic reason. Default crops
follow the displayed eligible pairs; frame-only pairs are rendered when requested
with `--show-frame`. Unrecognized frame formats may remain in circuit text, and
neither category proves a functional circuit change.
