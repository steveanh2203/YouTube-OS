"""Sora Gen API routes — bridge between YouTube OS và Chrome extension."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from autocapcut.database.connection import get_session
from autocapcut.database.models import ChildProject
from autocapcut.services.sora_service import (
    download_video,
    get_or_create_api_key,
    validate_api_key,
)
from loguru import logger

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Mỗi extension instance (worker) có ID riêng — track last seen timestamp
_workers: dict[str, float] = {}   # worker_id → last_seen timestamp
HEARTBEAT_TTL = 30  # seconds

# Lock để đảm bảo 2 workers không claim cùng 1 job
_next_job_lock = asyncio.Lock()


def _active_workers() -> dict[str, float]:
    """Trả về dict workers đã ping trong vòng HEARTBEAT_TTL giây."""
    now = time.time()
    return {wid: ts for wid, ts in _workers.items() if now - ts < HEARTBEAT_TTL}


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _require_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    if not validate_api_key(x_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")


# ---------------------------------------------------------------------------
# UI endpoints (không cần API key)
# ---------------------------------------------------------------------------

@router.get("/key")
def get_api_key() -> dict:
    """Trả về API key để user copy vào extension settings."""
    return {"key": get_or_create_api_key()}


@router.get("/connection-status")
def connection_status() -> dict:
    """Frontend poll endpoint — kiểm tra extension có đang online không."""
    active = _active_workers()
    return {
        "online": len(active) > 0,
        "worker_count": len(active),
        "last_seen": max(active.values(), default=None),
    }


@router.post("/queue/{child_project_id}")
async def queue_job(
    child_project_id: int,
    body: "QueueJobRequest",
    session: SessionDep,
) -> dict:
    """Frontend gọi để queue 1 job gen video cho child project."""
    child = await session.get(ChildProject, child_project_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found.")

    if child.sora_status == "pending" or child.sora_status == "generating":
        raise HTTPException(status_code=409, detail="A job is already running for this project.")

    child.sora_prompt = body.prompt.strip()
    child.sora_ratio = body.ratio or "16:9"
    child.sora_duration = body.duration or 5
    child.sora_status = "pending"
    child.sora_progress = 0
    child.sora_video_path = None
    child.sora_generation_id = None
    child.sora_permalink = None
    child.updated_at = datetime.utcnow()
    session.add(child)
    await session.commit()

    logger.info("Sora job queued for child_project_id={}", child_project_id)
    return {"ok": True, "child_project_id": child_project_id}


@router.get("/job-status/{child_project_id}")
async def job_status(child_project_id: int, session: SessionDep) -> dict:
    """Frontend poll để lấy tiến độ gen video."""
    child = await session.get(ChildProject, child_project_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found.")
    return {
        "sora_status": child.sora_status,
        "sora_progress": child.sora_progress,
        "sora_video_path": child.sora_video_path,
        "sora_permalink": child.sora_permalink,
        "sora_generation_id": child.sora_generation_id,
    }


# ---------------------------------------------------------------------------
# Extension endpoints (cần API key)
# ---------------------------------------------------------------------------

@router.post("/heartbeat", dependencies=[Depends(_require_key)])
def heartbeat(
    x_worker_id: str = Header(default="default", alias="X-Worker-Id"),
) -> dict:
    """Extension gọi mỗi 10s để báo online. Mỗi Chrome profile có worker_id riêng."""
    _workers[x_worker_id] = time.time()
    return {"ok": True, "worker_count": len(_active_workers())}


@router.get("/next-job", dependencies=[Depends(_require_key)])
async def next_job(
    session: SessionDep,
    x_worker_id: str = Header(default="default", alias="X-Worker-Id"),
) -> dict:
    """Extension poll — trả về job pending tiếp theo (nếu có).
    Dùng lock để tránh 2 workers cùng claim 1 job."""
    _workers[x_worker_id] = time.time()  # poll = heartbeat

    async with _next_job_lock:
        result = await session.execute(
            select(ChildProject)
            .where(ChildProject.sora_status == "pending")
            .order_by(ChildProject.updated_at.asc())
            .limit(1)
        )
        child = result.scalar_one_or_none()

        if not child:
            return {"job": None}

        # Atomically mark as generating trước khi release lock
        child.sora_status = "generating"
        child.sora_progress = 0
        child.updated_at = datetime.utcnow()
        session.add(child)
        await session.commit()

    logger.info("Sora next-job → child_project_id={} assigned to worker={}", child.id, x_worker_id)
    return {
        "job": {
            "id": child.id,
            "index": child.video_number,
            "prompt": child.sora_prompt,
            "ratio": child.sora_ratio or "16:9",
            "duration": child.sora_duration or 5,
        }
    }


@router.post("/progress", dependencies=[Depends(_require_key)])
async def report_progress(body: "ProgressRequest", session: SessionDep) -> dict:
    """Extension báo tiến độ % gen video."""
    child = await session.get(ChildProject, body.job_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found.")

    child.sora_progress = max(0, min(100, body.progress))
    child.updated_at = datetime.utcnow()
    session.add(child)
    await session.commit()
    return {"ok": True}


@router.post("/job-done", dependencies=[Depends(_require_key)])
async def job_done(body: "JobDoneRequest", session: SessionDep) -> dict:
    """Extension báo xong — backend tự download video về folder."""
    child = await session.get(ChildProject, body.job_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found.")

    if body.error:
        child.sora_status = "failed"
        child.sora_progress = 0
        child.updated_at = datetime.utcnow()
        session.add(child)
        await session.commit()
        logger.warning("Sora job failed for child_project_id={}: {}", body.job_id, body.error)
        return {"ok": True, "status": "failed"}

    # Xác định folder lưu video
    if child.base_folder_path:
        dest_folder = Path(child.base_folder_path)
    else:
        dest_folder = Path.home() / "Downloads" / "sora-videos"

    # Download video
    try:
        video_url = body.no_watermark_url or body.downloadable_url
        if not video_url:
            raise ValueError("No downloadable URL provided.")

        saved_path = await download_video(video_url, dest_folder)

        child.sora_status = "done"
        child.sora_progress = 100
        child.sora_video_path = str(saved_path)
        child.sora_generation_id = body.generation_id or None
        child.sora_permalink = body.public_permalink or None
        child.updated_at = datetime.utcnow()
        session.add(child)
        await session.commit()

        logger.info("Sora job done for child_project_id={} → {}", body.job_id, saved_path)
        return {"ok": True, "status": "done", "video_path": str(saved_path)}

    except Exception as exc:
        child.sora_status = "failed"
        child.updated_at = datetime.utcnow()
        session.add(child)
        await session.commit()
        logger.error("Sora download failed for child_project_id={}: {}", body.job_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class QueueJobRequest(BaseModel):
    prompt: str
    ratio: Optional[str] = "16:9"    # "16:9" | "9:16" | "1:1"
    duration: Optional[int] = 5      # 5 | 10 | 20


class ProgressRequest(BaseModel):
    job_id: int
    progress: int  # 0-100


class JobDoneRequest(BaseModel):
    job_id: int
    no_watermark_url: Optional[str] = None
    downloadable_url: Optional[str] = None
    generation_id: Optional[str] = None
    public_permalink: Optional[str] = None
    error: Optional[str] = None
