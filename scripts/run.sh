#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  exec uv run clipsnstrips "$@"
elif [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python -m clipsnstrips.cli "$@"
elif [[ -x ".venv/Scripts/python.exe" ]]; then
  exec .venv/Scripts/python.exe -m clipsnstrips.cli "$@"
else
  echo "Neither uv nor a project virtual environment was found. Run scripts/setup.sh first." >&2
  exit 1
fi
