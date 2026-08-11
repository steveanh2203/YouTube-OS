# Python Backend — MasterOS Web

FastAPI is the application server and production SPA host on port 8765.

## Structure

```text
autocapcut/
├── api/
│   ├── server.py       — app, CORS, routes, static SPA hosting
│   └── routes/         — one module per feature domain
├── services/           — framework-independent business logic
├── database/           — SQLModel schema and SQLite connection
├── render_v2/          — FFmpeg pipeline
├── configs/            — local credentials; never commit or log
└── utils/              — shared utilities
```

## Backend conventions

- Use async FastAPI handlers.
- Inject `AsyncSession` through `Depends(get_session)`.
- Use managed `media:`, `workspace:`, and `workspace-file:` references at the
  browser boundary instead of accepting arbitrary client filesystem paths.
- Add SQLite columns through the idempotent `_ensure_sqlite_columns()` helper.
- Use loguru or module loggers; do not use `print()`.
- Keep FFmpeg/ffprobe process execution in services, with validated inputs.

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

The production app is started with `./scripts/run_web.sh`; frontend development
uses Vite's same-origin proxy configuration.
