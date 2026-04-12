# Python Backend — autocapcut/

FastAPI sidecar cho Tauri desktop app. Chạy tại port 8765.

## Structure
```
autocapcut/
├── api/
│   ├── server.py          — FastAPI app + CORS + router registration
│   └── routes/            — một file per feature domain
├── services/              — business logic, không depend vào FastAPI
├── automation/            — CapCut GUI control (macOS-only)
├── database/
│   ├── models.py          — SQLModel table definitions
│   └── connection.py      — async engine + init_db()
├── render_v2/             — FFmpeg pipeline
├── configs/               — config.json (API cookies cho Wf Imagen — KHÔNG commit, KHÔNG log)
├── utils/                 — shared utilities
└── config.py              — CapCutConfig dataclass (project_root, timeouts, mock_mode)
```

## Patterns

### Thêm route mới
1. Tạo `api/routes/my_feature.py` với `router = APIRouter()`
2. Import và register trong `api/server.py`:
   ```python
   from autocapcut.api.routes import my_feature
   app.include_router(my_feature.router, prefix="/api/my-feature", tags=["my-feature"])
   ```

### DB access pattern
```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from autocapcut.database.connection import get_session

@router.get("/items")
async def list_items(session: AsyncSession = Depends(get_session)):
    ...
```

### Schema migrations
Không có Alembic. Thêm cột mới: edit `_ensure_sqlite_columns()` trong connection.py
```python
"my_table": {"new_column": "TEXT DEFAULT ''"},
```
Hàm này **idempotent** — nếu column đã tồn tại thì skip (không raise error).

## Logging
```python
from loguru import logger
logger.info("message")      # KHÔNG dùng print()
logger.debug("detail")
logger.warning("caution")
```

## Running tests
```bash
cd AutoCapCut
.venv/bin/python -m pytest tests/ -v
```

## macOS-only reminder
`automation/` dùng AtomAcos + PyObjC — không suggest cross-platform alternatives.
CapCut phải đang chạy cho bất kỳ automation call nào.
