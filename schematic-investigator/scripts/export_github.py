"""Export only maintained code/skill files into a new standalone repository folder."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    destination = args.destination.resolve()
    if destination.exists():
        parser.error("destination must not exist; choose a new directory")
    patterns = ["README.md", "pyproject.toml", ".gitignore", "src/**/*.py",
                "tests/*.py", "benchmarks/*.json", "skills/**/SKILL.md", "scripts/*.py", "scripts/*.sh"]
    files = sorted({path for pattern in patterns for path in source.glob(pattern)
                    if path.is_file() and not path.is_symlink()})
    destination.mkdir(parents=True)
    for path in files:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    print(f"Exported {len(files)} code/skill files to {destination}; no PDFs, reports or indexes included.")


if __name__ == "__main__":
    main()
