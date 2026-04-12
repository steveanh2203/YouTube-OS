#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_DIR="$ROOT_DIR/rust/audio_spectrum_renderer"

if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo is not installed. Please install Rust toolchain first." >&2
  exit 1
fi

echo "Building Rust Audio Spectrum renderer..."
cd "$ENGINE_DIR"
cargo build --release
echo "Done: $ENGINE_DIR/target/release/audio_spectrum_renderer"
