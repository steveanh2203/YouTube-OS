#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/frontend"

if [ ! -d "node_modules" ]; then
  npm install
fi

echo "[MasterOS] Building Tauri desktop shell..."
echo "[MasterOS] Note: the packaged app still expects the Python FastAPI backend on localhost:8765."
exec npm run tauri:build
