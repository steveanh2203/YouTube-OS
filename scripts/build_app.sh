#!/usr/bin/env bash
set -euo pipefail

# Resolve repository root (directory containing this script's parent).
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[AutoCapCut] Installing/updating dependencies…"
pip install -r requirements.txt

echo "[AutoCapCut] Cleaning previous build artefacts…"
rm -rf build dist AutoCapCut.spec.build

echo "[AutoCapCut] Building application bundle…"
pyinstaller --clean AutoCapCut.spec

# PyInstaller drops both a folder (`dist/AutoCapCut/…`) and the actual app bundle
# at `dist/AutoCapCut.app`. We want the latter so Finder shows the proper icon.
SOURCE_APP="$ROOT_DIR/dist/AutoCapCut.app"
TARGET_APP="/Applications/AutoCapCut.app"

if [[ ! -d "$SOURCE_APP" ]]; then
  echo "[AutoCapCut] ERROR: Expected bundle not found at $SOURCE_APP" >&2
  exit 1
fi

echo "[AutoCapCut] Linking bundle into /Applications…"
rm -rf "$TARGET_APP"
ln -s "$SOURCE_APP" "$TARGET_APP"

echo "[AutoCapCut] Done. Launch the app from Applications or pin it to the Dock."
