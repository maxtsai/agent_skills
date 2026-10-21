"""Create a local synthetic PDF for electrical-review.json; no user schematics."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fixtures import make_review_pdf


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output PDF; existing files are not overwritten")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(make_review_pdf(args.output).resolve())
