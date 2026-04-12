# Runbook: Rust SRT Engine

SRT matching engine viết bằng Rust — nhanh hơn Python ~10x cho video dài.
**Hoàn toàn optional** — Python fallback luôn hoạt động.

## Build

```bash
cd AutoCapCut
./scripts/build_rust_engine.sh
```

Output binary tại: `rust/srt_engine/target/release/srt_engine`

## Kiểm Tra Build OK

```bash
./rust/srt_engine/target/release/srt_engine --version
```

## Environment Variables

```bash
# Tự động: dùng Rust nếu có, Python nếu không
AUTOCAPCUT_SRT_ENGINE=auto   # default

# Bắt buộc dùng Rust (lỗi nếu chưa build)
AUTOCAPCUT_SRT_ENGINE=rust

# Bắt buộc dùng Python
AUTOCAPCUT_SRT_ENGINE=python

# Chỉ định binary path thủ công
AUTOCAPCUT_SRT_ENGINE_BIN=/path/to/srt_engine
```

## Khi Nào Nên Build

- Video dài > 30 phút: nên build để tránh timeout
- Dev thông thường: Python fallback là đủ
- Production / batch render: nên dùng Rust

## Rebuild Sau Khi Thay Đổi Rust Code

```bash
cd rust/srt_engine
cargo build --release
```

## Troubleshooting

**`cargo not found`:**
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

**Build lỗi dependency:**
```bash
cd rust/srt_engine
cargo clean
cargo build --release
```

**SRT matching không chính xác (dùng Rust):**
```bash
# Switch sang Python để so sánh kết quả
AUTOCAPCUT_SRT_ENGINE=python ./scripts/run_api.sh
```

**Kiểm tra engine nào đang được dùng:**
Xem log startup API — có dòng `[upscale]` và engine info.
