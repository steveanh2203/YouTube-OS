#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$ROOT_DIR/.venv" ]; then
  python3.11 -m venv "$ROOT_DIR/.venv"
fi

"$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"

cd "$ROOT_DIR/frontend"
if [ ! -d node_modules ]; then
  npm install
fi
npm run build

cd "$ROOT_DIR"
exec "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/start_api.py" --host 127.0.0.1 --port 8765
