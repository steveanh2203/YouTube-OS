# Runbook: Rust Audio Spectrum Renderer

Native renderer cho `spectrum_bars`.
Mục tiêu: thay phần Python raster bằng binary Rust, rồi tiến tiếp sang Metal backend.

## Build

```bash
./scripts/build_rust_audio_renderer.sh
```

Output binary:

```bash
rust/audio_spectrum_renderer/target/release/audio_spectrum_renderer
```

## Environment Variables

```bash
# auto: hiện vẫn đi Python ổn định, dùng cho production path hiện tại
AUTOCAPCUT_AUDIO_VIS_ENGINE=auto

# bật native Rust path để benchmark/dev
AUTOCAPCUT_AUDIO_VIS_ENGINE=rust

# bắt buộc dùng Python
AUTOCAPCUT_AUDIO_VIS_ENGINE=python

# custom binary path
AUTOCAPCUT_AUDIO_VIS_ENGINE_BIN=/path/to/audio_spectrum_renderer

# raster backend bên trong binary Rust
AUTOCAPCUT_AUDIO_VIS_RUST_BACKEND=auto
AUTOCAPCUT_AUDIO_VIS_RUST_BACKEND=cpu
AUTOCAPCUT_AUDIO_VIS_RUST_BACKEND=metal
```

## Current Scope

- chỉ phục vụ `spectrum_bars`
- giữ form render bám preview hiện tại
- đã có `Metal` compute shader riêng, và giữ `device/pipeline/buffer` theo kiểu reuse
- chưa bật mặc định vì benchmark full pipeline hiện vẫn chậm hơn Python path hiện tại

## Verify

```bash
PYTHONPATH=. .venv/bin/python -m unittest tests.test_audio_visualizer -v
```

## Latest Benchmarks

Clip test: `20s`, `spectrum_bars`, output `1920x1080`

- renderer-only:
  - `rust-cpu`: `~0.62s`
  - `rust-metal`: `~1.61s`
- full render:
  - `python`: `~41.36s`
  - `rust-cpu`: `~76.02s`
  - `rust-metal`: `~76.20s`

Kết luận hiện tại:

- native foundation đã xong
- `Metal` không còn bị lỗi init mỗi frame
- nhưng bottleneck full pipeline vẫn nằm ngoài chỗ raster thuần, nên `auto` vẫn phải giữ Python path
