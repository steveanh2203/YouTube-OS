# Architecture — MasterOS Web

## Overview

MasterOS is a single-user, self-hosted web application. The browser UI talks to a
FastAPI server over same-origin HTTP and WebSocket endpoints. The server owns the
SQLite database, managed media workspace, and FFmpeg processes.

```text
Browser
  └── React + Vite UI
        ↕ /api, /media, WebSocket
FastAPI (127.0.0.1:8765)
  ├── serves frontend/dist as an SPA
  ├── API routes and background jobs
  ├── SQLite
  ├── managed media workspace
  └── FFmpeg / ffprobe / optional Rust engines
```

There is no desktop shell or GUI automation layer. File input uses browser
uploads and managed workspace references; output is previewed or downloaded by
the browser.

## Frontend

- React 19, TypeScript, Vite, Tailwind CSS, Radix UI, Zustand.
- Development server: `http://127.0.0.1:1420` with proxies for `/api` and
  `/media`.
- Production: FastAPI serves the built `frontend/dist` directory.
- Navigation uses the existing Zustand view state rather than React Router.
- Feature pages are lazy-loaded to keep the initial bundle small.
- API and WebSocket helpers derive their origin from the current page.

## Backend

- Entry point: `main.py` → `autocapcut/api/server.py`.
- Default address: `127.0.0.1:8765`.
- API routers live in `autocapcut/api/routes/`.
- Business logic lives in `autocapcut/services/`.
- FFmpeg pipelines live in `autocapcut/render_v2/` and media services.
- CORS permits the local Vite development origins; production is same-origin.

## Managed files

Browser clients never send arbitrary server file paths for normal workflows.
The API returns opaque references:

- `media:<id>` for Media Library assets.
- `workspace:<id>` for managed directories.
- `workspace-file:<directory-id>:<relative-path>` for files inside them.

The default root is `~/.autocapcut/workspace`. Override it with
`AUTOCAPCUT_WORKSPACE_DIR`.

## Database

- SQLite via SQLModel and aiosqlite.
- Default path: `~/.autocapcut/autocapcut.db`.
- Schema: `autocapcut/database/models.py`.
- Idempotent compatibility migrations: `_ensure_sqlite_columns()` in
  `autocapcut/database/connection.py`.

## Media processing

FFmpeg and ffprobe remain first-class runtime dependencies. They power fast
editing, cut/batch jobs, silence removal, audio visualization, metadata work,
and media inspection. Real-ESRGAN and the Rust SRT/audio engines remain optional.

## Runtime flows

```text
Upload/import in browser
  → Media Library or managed workspace
  → submit API job using an opaque reference
  → server resolves the reference inside the managed roots
  → FFmpeg/service writes managed output
  → browser previews or downloads the result
```

## Processes

| Process | Port | Command |
|---|---:|---|
| Production web app | 8765 | `./scripts/run_web.sh` |
| Backend development | 8765 | `./scripts/run_api.sh --port 8765` |
| Frontend development | 1420 | `npm run dev` in `frontend/` |
