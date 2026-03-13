#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

"$ROOT_DIR/.venv/bin/python" -m pip install -r requirements.txt
exec "$ROOT_DIR/.venv/bin/python" main.py
