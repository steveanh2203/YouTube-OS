# Architecture — AutoCapCut / MasterOS

## Overview

MasterOS là desktop app macOS-only, kết hợp 3 layers:

```
┌─────────────────────────────────────────────┐
│              Tauri Shell (Rust)              │  ← Desktop window, file dialogs, OS integration
├─────────────────────────────────────────────┤
│           React UI  (port 1420 dev)          │  ← Frontend, Vite, Tailwind, Radix UI
├─────────────────────────────────────────────┤
│       FastAPI Sidecar  (port 8765)           │  ← HTTP API bridge
├─────────────────────────────────────────────┤
│          autocapcut/ Python package          │
│  ┌──────────┬───────────┬─────────────────┐ │
│  │ services │ automation│   render_v2     │ │
│  │ (logic)  │ (CapCut   │   (FFmpeg)      │ │
│  │          │  GUI ctrl)│                 │ │
│  └──────────┴───────────┴─────────────────┘ │
├─────────────────────────────────────────────┤
│      SQLite  (~/.autocapcut/autocapcut.db)   │  ← Local DB, no server needed
└─────────────────────────────────────────────┘
```

## Layer Chi Tiết

### 1. Tauri Shell (Rust)
- File: `frontend/src-tauri/`
- Config: `frontend/src-tauri/tauri.conf.json`
- Vai trò: Desktop window, native OS dialogs (`@tauri-apps/plugin-dialog`)
- **Không chứa business logic** — chỉ là shell

### 2. React Frontend
- Stack: React 19 + TypeScript + Vite 8 + Tailwind CSS 3
- State: Zustand (`store/app.store.ts`, `store/task.store.ts`, `store/toast.store.ts`)
- Navigation: Không có React Router — dùng `setMainView()` / `setPanelMainView()` trong Zustand
- UI Primitives: Radix UI (Dialog, Dropdown, Tabs, Select, Switch, Tooltip...)
- Animation: Framer Motion
- **Gọi API trực tiếp** qua `fetch('http://127.0.0.1:8765/api/...')`

### 3. FastAPI Sidecar
- Entry: `main.py` → `autocapcut/api/server.py`
- Port: `8765`
- CORS: allow all origins (Tauri WebView dùng null origin)
- Startup: tự init DB + pre-warm Real-ESRGAN binary

### 4. Python Package (`autocapcut/`)

```
autocapcut/
├── api/routes/      — FastAPI routers, 1 file per domain
├── services/        — Business logic (không depend FastAPI)
├── automation/      — CapCut GUI control via AtomAcos + PyAutoGUI
├── database/        — SQLModel + aiosqlite
├── render_v2/       — FFmpeg pipeline
├── configs/         — API credentials (KHÔNG commit)
└── config.py        — CapCutConfig (paths, timeouts)
```

### 5. Database
- Engine: SQLite via SQLModel + aiosqlite
- Path: `~/.autocapcut/autocapcut.db`
- Migration: manual ALTER TABLE trong `_ensure_sqlite_columns()` (idempotent)
- Schema: xem `autocapcut/database/models.py`

## Data Model

```
ParentProject (kênh / series)
    └── ChildProject (từng video)
            ├── Caption (SRT versions)
            ├── SeoMetadata
            ├── RenderHistory
            ├── Thumbnail
            ├── ChildFolder (thư mục trên disk)
            └── CompetitorVideo
```

## Optional Components

### Rust SRT Engine
- Path: `rust/srt_engine/`
- Kiểm soát: env var `AUTOCAPCUT_SRT_ENGINE=auto|rust|python`
- Mặc định `auto`: ưu tiên Rust, fallback Python nếu chưa build
- Build: `./scripts/build_rust_engine.sh`

### Real-ESRGAN Upscaling
- Service: `autocapcut/services/upscale_engine.py`
- Pre-warmed lúc startup để lần đầu gọi không bị chậm
- Binary được cache trong memory sau lần đầu

## Flow Điển Hình: Export Video

```
User click "Export" trong UI
    → fetch POST /api/srt/generate
    → srt_generator.py đọc CapCut draft JSON
    → (optional) Rust engine match SRT segments
    → trả về SRT content
    → fetch POST /api/sync/captions
    → sync_captions.py điều chỉnh image durations
    → fetch POST /api/render
    → ffmpeg_render.py batch render
    → UI poll status qua task.store.ts
```

## Ports & Processes

| Process | Port | Khởi động bằng |
|---|---|---|
| Python FastAPI | 8765 | `./scripts/run_api.sh` |
| Vite dev server | 1420 | `npm run dev` (trong frontend/) |
| Tauri shell | — | `npm run tauri:dev` |
