#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_DIR="$ROOT_DIR/rust/srt_engine"

if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo is not installed. Please install Rust toolchain first." >&2
  exit 1
fi

echo "Building Rust SRT engine..."
cd "$ENGINE_DIR"
cargo build --release
echo "Done: $ENGINE_DIR/target/release/srt_engine"
