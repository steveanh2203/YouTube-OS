"""SQLite engine + async session management (aiosqlite)."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

# Lưu DB trong thư mục home của user (~/.autocapcut/autocapcut.db)
DB_DIR = Path.home() / ".autocapcut"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "autocapcut.db"

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

# Keep sync engine alias for backward-compat (init_db still uses metadata.create_all)
# SQLModel.metadata.create_all requires a sync engine; we create a throwaway one.
_SYNC_URL = f"sqlite:///{DB_PATH}"


def init_db() -> None:
    """Tạo tất cả các tables nếu chưa có (sync, chạy lúc startup)."""
    from sqlalchemy import create_engine as _create_engine
    from autocapcut.database import models  # noqa: F401

    _sync_engine = _create_engine(_SYNC_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(_sync_engine)
    _ensure_sqlite_columns(_sync_engine)
    _sync_engine.dispose()


def _ensure_sqlite_columns(engine) -> None:
    required_columns = {
        "parent_projects": {
            "roxy_workspace_id": "INTEGER",
            "roxy_profile_id": "TEXT",
            "roxy_profile_name": "TEXT",
        },
        "child_projects": {
            "roxy_workspace_id": "INTEGER",
            "roxy_profile_id": "TEXT",
            "roxy_profile_name": "TEXT",
            "planning_stage": "TEXT DEFAULT 'backlog'",
            "priority": "TEXT DEFAULT 'medium'",
            "deadline": "DATE",
            "planning_note": "TEXT DEFAULT ''",
            "sora_prompt": "TEXT",
            "sora_status": "TEXT",
            "sora_progress": "INTEGER DEFAULT 0",
            "sora_video_path": "TEXT",
            "sora_generation_id": "TEXT",
            "sora_permalink": "TEXT",
            "sora_ratio": "TEXT DEFAULT '16:9'",
            "sora_duration": "INTEGER DEFAULT 5",
        },
    }

    with engine.begin() as conn:
        for table_name, columns in required_columns.items():
            existing = {
                str(row[1])
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
            }
            for column_name, column_type in columns.items():
                if column_name in existing:
                    continue
                conn.exec_driver_sql(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                )

        conn.exec_driver_sql(
            """
            UPDATE child_projects
            SET planning_stage = CASE
                WHEN status = 'published' THEN 'published'
                WHEN status = 'editing' THEN 'editing'
                WHEN status = 'resource_prep' THEN 'ready'
                ELSE 'backlog'
            END
            WHERE planning_stage IS NULL OR planning_stage = ''
            """
        )
        conn.exec_driver_sql(
            "UPDATE child_projects SET priority = 'medium' WHERE priority IS NULL OR priority = ''"
        )
        conn.exec_driver_sql(
            "UPDATE child_projects SET planning_note = '' WHERE planning_note IS NULL"
        )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI async dependency — cấp phát AsyncSession cho mỗi request."""
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session
