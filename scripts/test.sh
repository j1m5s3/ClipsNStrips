#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  uv run ruff format --check .
  uv run ruff check .
  uv run pytest
elif [[ -x ".venv/bin/python" ]]; then
  .venv/bin/python -m ruff format --check .
  .venv/bin/python -m ruff check .
  .venv/bin/python -m pytest
elif [[ -x ".venv/Scripts/python.exe" ]]; then
  .venv/Scripts/python.exe -m ruff format --check .
  .venv/Scripts/python.exe -m ruff check .
  .venv/Scripts/python.exe -m pytest
else
  echo "No project environment found. Run scripts/setup.sh first." >&2
  exit 1
fi
