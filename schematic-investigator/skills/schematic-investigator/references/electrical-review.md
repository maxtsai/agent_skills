# Electrical review regression

Read this for skill reasoning validation, not ordinary retrieval. In a repository
checkout, generate the synthetic PDF with `python scripts/make_review_fixture.py
<new-local.pdf>` using an environment with PyMuPDF. Index it, then use the normal
search/crop workflow. `benchmarks/electrical-review.json` supplies the benchmark.
No user schematic content is included.

Inspect page 3 visually and write a structured report before looking at the review
criteria below. For an independent evaluation, give the evaluator only the PDF,
skill and task; retain these criteria with the reviewer.

## Reviewer criteria

- R1 goes down to a ground symbol. VCC_3V3 text is nearby but has no connecting
  supply wire. Calling R1 a pull-up from proximity is an error.
- R2 is annotated NO STUFF; a printed value does not establish population.
- Crossing lines have no junction dot. Require sheet conventions or a netlist
  before claiming electrical continuity; crossing alone is insufficient.
- GPIO_REVIEW has no direction or pinmux evidence. A name cannot establish output.
- RAIL_HOST_3V3 and RAIL_CARD_3V3 are different labels. Equal nominal voltages do
  not establish one electrical rail.
- RST_REVIEW_N has no measured relationship to 12V_CARD. A reset claim cannot be
  promoted to a measured power cycle.
- Pages 1/2 have repeated A13 text on two connectors and a DECOY_NET on the same
  row without a wire. Scope the intended connector; confirm net paths visually.
  A1, A13 and OA13 must remain distinct. Rotated B26 requires visual review.

## What automated checks establish

The benchmark requires structured reports, validates citation provenance, flags
declared confirmed connectivity claims, and checks specific forbidden phrases.
It does not grade arbitrary paraphrases, recognize every electrical error, or
prove correct reasoning. Empty claim lists do not demonstrate useful diagnosis.
Human review must assess the actual observations, conditions and proposed
measurements. Keep measured retrieval results separate from reasoning assessments.
