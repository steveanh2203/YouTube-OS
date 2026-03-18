# MasterOS

UI hiện tại dùng `Tauri + React + Vite`, còn Python chỉ giữ vai trò `FastAPI` backend cho các service xử lý.

## Prerequisites
- Python 3.11+
- Node.js 20+
- Rust toolchain
- FFmpeg/ffprobe trong `PATH`

## Run Desktop App
```bash
./scripts/run_app.sh
```

## Run API Only
```bash
./scripts/run_api.sh --port 8765
```

## Build Desktop App
```bash
./scripts/build_app.sh
```

Bundle Tauri hiện mới đóng gói desktop shell; các tính năng gọi API vẫn cần Python backend chạy ở `127.0.0.1:8765`.

## Rust Engine (tuỳ chọn cho SRT matching)
```bash
./scripts/build_rust_engine.sh
```

- `AUTOCAPCUT_SRT_ENGINE=auto`: ưu tiên Rust, lỗi thì fallback Python
- `AUTOCAPCUT_SRT_ENGINE=rust`: bắt buộc Rust
- `AUTOCAPCUT_SRT_ENGINE=python`: dùng matcher Python
- `AUTOCAPCUT_SRT_ENGINE_BIN=/path/to/srt_engine`: chỉ định binary thủ công
