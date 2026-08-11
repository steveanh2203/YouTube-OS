# MasterOS Web

MasterOS là web app single-user self-hosted cho quy trình sản xuất YouTube. React chạy trong browser, FastAPI quản lý SQLite và Media Library, còn FFmpeg/ffprobe xử lý media ở phía server.

Desktop shell và phần tự động hóa GUI đã được loại bỏ. FFmpeg vẫn là runtime xử lý media chính.

## Yêu cầu

- macOS
- Python 3.11+
- Node.js 20+
- FFmpeg và ffprobe trong `PATH`
- Rust chỉ cần khi muốn dùng SRT engine tùy chọn

## Chạy web app

```bash
./scripts/run_web.sh
```

Sau khi build xong, mở [http://127.0.0.1:8765](http://127.0.0.1:8765). Server mặc định chỉ bind localhost vì bản single-user hiện chưa có đăng nhập.

Media upload và file kết quả được quản lý dưới `~/.autocapcut/workspace`. Có thể đổi thư mục này bằng `AUTOCAPCUT_WORKSPACE_DIR`.

## Phát triển frontend

```bash
./scripts/run_api.sh --port 8765
cd frontend
npm install
npm run dev
```

Vite proxy `/api` và `/media` sang FastAPI nên frontend luôn dùng URL same-origin.

## Kiểm thử

```bash
.venv/bin/python -m pytest tests/
cd frontend
npm run test:web
npm run build
```

## Rust SRT engine (tùy chọn)

```bash
./scripts/build_rust_engine.sh
```

- `AUTOCAPCUT_SRT_ENGINE=auto`: ưu tiên Rust, lỗi thì fallback Python
- `AUTOCAPCUT_SRT_ENGINE=rust`: bắt buộc Rust
- `AUTOCAPCUT_SRT_ENGINE=python`: dùng matcher Python
- `AUTOCAPCUT_SRT_ENGINE_BIN=/path/to/srt_engine`: chỉ định binary thủ công
