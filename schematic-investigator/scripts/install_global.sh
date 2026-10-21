#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
skill_dir="${CODEX_HOME:-$HOME/.codex}/skills/schematic-investigator"
mkdir -p "$skill_dir"
python3 -m venv "$skill_dir/.venv"
"$skill_dir/.venv/bin/python" -m pip install "$repo_dir"
cp "$repo_dir/skills/schematic-investigator/SKILL.md" "$skill_dir/SKILL.md"
cp "$repo_dir/README.md" "$skill_dir/README.md"
"$skill_dir/.venv/bin/schematic-investigator" search --help
printf 'Installed skill: %s\n' "$skill_dir"
