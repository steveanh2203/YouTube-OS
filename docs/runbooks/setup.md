# Runbook: First-Time Setup

## Prerequisites

Cài trước khi bắt đầu:

```bash
# Kiểm tra
python3 --version    # cần 3.11+
node --version       # cần 20+
cargo --version      # Rust toolchain
ffmpeg -version      # FFmpeg
ffprobe -version     # ffprobe (thường đi kèm FFmpeg)
```

Cài FFmpeg nếu chưa có:
```bash
brew install ffmpeg
```

Cài Rust nếu chưa có:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

## Setup Steps

```bash
# 1. Clone / mở project
cd AutoCapCut

# 2. Tạo Python virtual environment
python3.11 -m venv .venv

# 3. Cài Python dependencies
.venv/bin/pip install -r requirements.txt

# 4. Cài Node dependencies
cd frontend && npm install && cd ..

# 5. (Optional) Build Rust SRT engine
./scripts/build_rust_engine.sh
```

## Chạy App

```bash
# Full app (Tauri + FastAPI) — dùng hàng ngày
./scripts/run_app.sh

# API only — khi dev backend
./scripts/run_api.sh --port 8765

# Frontend dev với HMR — khi dev UI
cd frontend && npm run dev:api
```

## Kiểm Tra Setup OK

```bash
# Kiểm tra API chạy
curl http://127.0.0.1:8765/api/projects

# Kiểm tra DB được tạo
ls ~/.autocapcut/autocapcut.db
```

## Cài Thêm Package Python

```bash
# LUÔN dùng .venv — không dùng pip trực tiếp
.venv/bin/pip install <package>

# Sau đó update requirements.txt
.venv/bin/pip freeze > requirements.txt
```

## Troubleshooting

**Lỗi `port 8765 already in use`:**
```bash
lsof -ti:8765 | xargs kill -9
```

**Lỗi `.venv not found`:**
```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

**Lỗi `node_modules not found`:**
```bash
cd frontend && npm install
```
