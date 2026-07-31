#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  uv sync --dev
elif [[ -x ".venv/bin/python" ]]; then
  echo "uv is not on PATH; bootstrapping it in the existing environment."
  .venv/bin/python -m ensurepip
  .venv/bin/python -m pip install uv
  .venv/bin/python -m uv sync --dev
elif [[ -x ".venv/Scripts/python.exe" ]]; then
  echo "uv is not on PATH; bootstrapping it in the existing Windows environment."
  .venv/Scripts/python.exe -m ensurepip
  .venv/Scripts/python.exe -m pip install uv
  .venv/Scripts/python.exe -m uv sync --dev
else
  echo "uv was not found. Install it from https://docs.astral.sh/uv/ and rerun." >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "Created .env from .env.example; add credentials before running live commands."
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "Warning: FFmpeg and FFprobe must be installed before media processing." >&2
fi

echo "ClipsNStrips setup complete."
