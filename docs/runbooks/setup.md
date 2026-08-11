# Runbook: First-Time Web Setup

## Prerequisites

- macOS
- Python 3.11+
- Node.js 20+
- FFmpeg and ffprobe in `PATH`
- Rust toolchain only for optional native engines

```bash
python3 --version
node --version
ffmpeg -version
ffprobe -version
```

Install FFmpeg with Homebrew if needed:

```bash
brew install ffmpeg
```

## One-command setup and start

From the repository root:

```bash
./scripts/run_web.sh
```

The script creates `.venv` when needed, installs missing project dependencies,
builds the React frontend, and starts FastAPI. Open
`http://127.0.0.1:8765` after the build completes.

The default localhost binding is deliberate: this single-user version has no
authentication and must not be exposed directly to the public internet.

## Manual setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && cd ..
```

Optional Rust SRT engine:

```bash
./scripts/build_rust_engine.sh
```

## Development

Terminal 1:

```bash
./scripts/run_api.sh --port 8765
```

Terminal 2:

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:1420`. Vite proxies API and media requests to FastAPI.

## Verification

```bash
curl http://127.0.0.1:8765/api/health
.venv/bin/python -m pytest tests/
cd frontend
npm run lint
npm run test:web
npm run build
```

Runtime data is stored under `~/.autocapcut/` for compatibility with existing
installations. Managed media defaults to `~/.autocapcut/workspace` and can be
relocated with `AUTOCAPCUT_WORKSPACE_DIR`.
