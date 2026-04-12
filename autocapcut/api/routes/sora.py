"""Sora Gen API routes — bridge between YouTube OS and the Sora extension worker."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from autocapcut.database.connection import get_session
from autocapcut.database.models import ChildProject, SoraJob
from autocapcut.services.sora_service import (
    clear_managed_videos,
    download_video,
    enhance_video_1080p,
    get_or_create_api_key,
    list_video_files,
    mask_api_key,
    next_video_path,
    save_uploaded_video,
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

# In-memory job logs — ephemeral, not persisted to DB
_job_logs: dict[int, list[dict]] = {}   # job_id → list of log entries
MAX_JOB_LOGS = 300  # per job

# ── Concurrent thread config (in-memory, reset on server restart) ──────────────
_sora_config: dict = {
    "max_threads": 3,          # sora hiện chỉ chạy ổn tối đa 3 job song song
    "thread_start_delay": 0,   # giây tối thiểu giữa 2 job start (anti-ban)
}
_last_job_started_at: float = 0.0   # timestamp của job gần nhất được giao


class SoraRealtimeManager:
    """Realtime bus cho desktop app và Sora worker."""

    def __init__(self) -> None:
        self._desktop_clients: set[WebSocket] = set()
        self._worker_clients: dict[str, WebSocket] = {}

    async def connect(self, ws: WebSocket, client: str, worker_id: str | None = None) -> None:
        await ws.accept()
        if client == "worker" and worker_id:
            old = self._worker_clients.get(worker_id)
            if old and old is not ws:
                try:
                    await old.close()
                except Exception:
                    pass
            self._worker_clients[worker_id] = ws
            return
        self._desktop_clients.add(ws)

    def disconnect(self, ws: WebSocket, client: str, worker_id: str | None = None) -> None:
        if client == "worker" and worker_id:
            current = self._worker_clients.get(worker_id)
            if current is ws:
                self._worker_clients.pop(worker_id, None)
            return
        self._desktop_clients.discard(ws)

    @property
    def worker_ids(self) -> set[str]:
        return set(self._worker_clients.keys())

    async def broadcast(self, data: dict) -> None:
        dead_desktop: set[WebSocket] = set()
        dead_workers: list[str] = []

        for client in list(self._desktop_clients):
            try:
                await client.send_json(data)
            except Exception:
                dead_desktop.add(client)

        for worker_id, client in list(self._worker_clients.items()):
            try:
                await client.send_json(data)
            except Exception:
                dead_workers.append(worker_id)

        for client in dead_desktop:
            self._desktop_clients.discard(client)
        for worker_id in dead_workers:
            self._worker_clients.pop(worker_id, None)


_realtime = SoraRealtimeManager()


def _active_workers() -> dict[str, float]:
    """Trả về dict workers đã ping trong vòng HEARTBEAT_TTL giây."""
    now = time.time()
    active = {wid: ts for wid, ts in _workers.items() if now - ts < HEARTBEAT_TTL}
    for wid in _realtime.worker_ids:
        active[wid] = max(active.get(wid, 0.0), now)
    return active


def _serialize_job(job: SoraJob) -> dict:
    return {
        "id": getattr(job, "id", None),
        "job_index": getattr(job, "job_index", 0),
        "prompt": getattr(job, "prompt", ""),
        "ratio": getattr(job, "ratio", None),
        "duration": getattr(job, "duration", None),
        "status": getattr(job, "status", "pending"),
        "progress": getattr(job, "progress", 0),
        "video_path": getattr(job, "video_path", None),
        "generation_id": getattr(job, "generation_id", None),
        "permalink": getattr(job, "permalink", None),
        "error_msg": getattr(job, "error_msg", None),
    }


async def _broadcast_presence() -> None:
    active = _active_workers()
    await _realtime.broadcast({
        "type": "sora_presence",
        "online": len(active) > 0,
        "worker_count": len(active),
        "last_seen": max(active.values(), default=None),
    })


async def _broadcast_config() -> None:
    await _realtime.broadcast({
        "type": "sora_config",
        "config": _sora_config.copy(),
    })


async def _broadcast_queue_hint(child_project_id: int) -> None:
    await _realtime.broadcast({
        "type": "sora_queue_hint",
        "child_project_id": child_project_id,
        "ts": time.time(),
    })


async def _broadcast_batch_start(child_project_id: int, prompt_count: int) -> None:
    await _realtime.broadcast({
        "type": "sora_batch_start",
        "child_project_id": child_project_id,
        "prompt_count": prompt_count,
        "ts": time.time(),
    })


async def _broadcast_jobs(session: AsyncSession, child_project_id: int) -> None:
    result = await session.execute(
        select(SoraJob)
        .where(SoraJob.child_project_id == child_project_id)
        .order_by(SoraJob.job_index.asc())
    )
    jobs = result.scalars().all()
    await _realtime.broadcast({
        "type": "sora_jobs",
        "child_project_id": child_project_id,
        "jobs": [_serialize_job(job) for job in jobs],
    })


async def _broadcast_job_patch(job: SoraJob) -> None:
    await _realtime.broadcast({
        "type": "sora_job_patch",
        "child_project_id": job.child_project_id,
        "job": _serialize_job(job),
    })


async def _append_internal_job_log(job_id: int, level: str, message: str) -> None:
    entries = _job_logs.setdefault(job_id, [])
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "level": level,
        "message": message,
    }
    entries.append(entry)
    _job_logs[job_id] = entries[-MAX_JOB_LOGS:]
    await _realtime.broadcast({
        "type": "sora_job_log",
        "job_id": job_id,
        "entry": entry,
    })


async def _set_job_progress(session: AsyncSession, job: SoraJob, progress: int) -> None:
    job.progress = max(0, min(100, progress))
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    await _broadcast_job_patch(job)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _require_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    if not validate_api_key(x_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")


# ---------------------------------------------------------------------------
# Concurrent config endpoints
# ---------------------------------------------------------------------------

@router.get("/config")
def get_sora_config() -> dict:
    return _sora_config.copy()


class SoraConfigPatch(BaseModel):
    max_threads: Optional[int] = None       # cố định 3
    thread_start_delay: Optional[int] = None  # seconds, 0–300


@router.patch("/config")
async def patch_sora_config(body: SoraConfigPatch) -> dict:
    if body.max_threads is not None:
        _sora_config["max_threads"] = 3
    if body.thread_start_delay is not None:
        _sora_config["thread_start_delay"] = max(0, min(300, body.thread_start_delay))
    await _broadcast_config()
    return _sora_config.copy()


# ---------------------------------------------------------------------------
# UI endpoints (không cần API key)
# ---------------------------------------------------------------------------

@router.get("/key")
def get_api_key() -> dict:
    """Return the bridge key so the desktop UI can show it to the user."""
    return {"key": get_or_create_api_key()}


class VerifyKeyRequest(BaseModel):
    api_key: str


@router.post("/verify-key")
def verify_key(body: "VerifyKeyRequest") -> dict:
    """Verify the extension bridge key before the worker starts polling."""
    api_key = body.api_key.strip()
    if not api_key or not validate_api_key(api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")
    return {
        "ok": True,
        "connected": True,
        "masked_key": mask_api_key(api_key),
        "worker_count": len(_active_workers()),
    }


@router.get("/connection-status")
def connection_status() -> dict:
    """Frontend poll endpoint — show whether any Sora worker is alive."""
    active = _active_workers()
    return {
        "online": len(active) > 0,
        "worker_count": len(active),
        "last_seen": max(active.values(), default=None),
    }


@router.post("/queue-batch/{child_project_id}")
async def queue_batch(
    child_project_id: int,
    body: "QueueBatchRequest",
    session: SessionDep,
) -> dict:
    """Frontend gọi để queue nhiều prompts cho child project."""
    child = await session.get(ChildProject, child_project_id)
    if not child:
        raise HTTPException(status_code=404, detail="Child project not found.")

    if not body.prompts:
        raise HTTPException(status_code=400, detail="No prompts provided.")

    # Xoá các jobs cũ pending/failed trước khi queue mới (giữ lại done để reference)
    old_result = await session.execute(
        select(SoraJob)
        .where(SoraJob.child_project_id == child_project_id)
        .where(SoraJob.status.in_(["pending", "failed"]))
    )
    for old_job in old_result.scalars().all():
        await session.delete(old_job)

    # Tạo jobs mới
    new_jobs: list[SoraJob] = []
    for idx, prompt_text in enumerate(body.prompts, start=1):
        job = SoraJob(
            child_project_id=child_project_id,
            job_index=idx,
            prompt=prompt_text.strip(),
            ratio=body.ratio or "16:9",
            duration=body.duration or 5,
            status="pending",
            progress=0,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(job)
        new_jobs.append(job)

    await session.commit()
    for job in new_jobs:
        await session.refresh(job)

    logger.info(
        "Sora queue-batch: {} jobs queued for child_project_id={}",
        len(new_jobs), child_project_id,
    )
    await _broadcast_batch_start(child_project_id, len(new_jobs))
    await _broadcast_jobs(session, child_project_id)
    await _broadcast_queue_hint(child_project_id)
    return {"ok": True, "job_count": len(new_jobs)}


@router.post("/stop-batch/{child_project_id}")
async def stop_batch(child_project_id: int, session: SessionDep) -> dict:
    """Frontend gọi để dừng tất cả pending/generating jobs của child project."""
    result = await session.execute(
        select(SoraJob)
        .where(SoraJob.child_project_id == child_project_id)
        .where(SoraJob.status.in_(["pending", "generating"]))
    )
    jobs = result.scalars().all()
    for job in jobs:
        job.status = "failed"
        job.error_msg = "Stopped by user."
        job.updated_at = datetime.utcnow()
    await session.commit()
    logger.info("Sora stop-batch: {} jobs cancelled for child_project_id={}", len(jobs), child_project_id)
    await _broadcast_jobs(session, child_project_id)
    return {"ok": True, "cancelled": len(jobs)}


@router.get("/jobs/{child_project_id}")
async def list_jobs(child_project_id: int, session: SessionDep) -> dict:
    """Frontend poll — lấy tất cả SoraJob của child project."""
    result = await session.execute(
        select(SoraJob)
        .where(SoraJob.child_project_id == child_project_id)
        .order_by(SoraJob.job_index.asc())
    )
    jobs = result.scalars().all()
    return {
        "jobs": [_serialize_job(j) for j in jobs]
    }


@router.get("/job/{job_id}")
async def get_job_detail(job_id: int, session: SessionDep) -> dict:
    """Worker poll nhẹ để biết job còn được phép retry hay đã bị user stop."""
    job = await session.get(SoraJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sora job not found.")
    return {"job": _serialize_job(job)}


@router.get("/download/{job_id}")
async def download_job_file(job_id: int, session: SessionDep) -> FileResponse:
    """Download the saved local video for a finished Sora job."""
    job = await session.get(SoraJob, job_id)
    if not job or not job.video_path:
        raise HTTPException(status_code=404, detail="Sora video not found.")

    video_path = Path(job.video_path).expanduser()
    if not video_path.exists() or not video_path.is_file():
        raise HTTPException(status_code=404, detail="Saved Sora video is missing on disk.")

    return FileResponse(
        path=video_path,
        filename=video_path.name,
        media_type="video/mp4",
    )


@router.post("/output-folder/inspect")
async def inspect_output_folder(body: "OutputFolderInspectRequest") -> dict:
    folder = Path(body.folder).expanduser()
    video_paths = list_video_files(folder)
    return {
        "folder": str(folder),
        "exists": folder.is_dir(),
        "video_count": len(video_paths),
        "video_sample_names": [path.name for path in video_paths[:3]],
    }


@router.post("/output-folder/init")
async def init_output_folder(body: "OutputFolderInitRequest") -> dict:
    folder = Path(body.folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    mode = (body.conflict_mode or "keep_both").strip().lower()
    if mode not in {"keep_both", "replace_all"}:
        raise HTTPException(status_code=400, detail="Invalid output conflict mode.")

    removed = clear_managed_videos(folder) if mode == "replace_all" else 0
    return {
        "folder": str(folder),
        "conflict_mode": mode,
        "removed_video_count": removed,
    }


@router.post("/output-folder/open")
async def open_output_folder(body: "OutputFolderOpenRequest") -> dict:
    folder = Path(body.folder).expanduser()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Output folder not found on disk.")

    if sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    elif sys.platform == "win32":
        os.startfile(str(folder))
    else:
        subprocess.Popen(["xdg-open", str(folder)])

    return {"ok": True, "folder": str(folder)}


# Legacy endpoint — kept for backward compat
@router.get("/job-status/{child_project_id}")
async def job_status(child_project_id: int, session: SessionDep) -> dict:
    """Legacy — lấy sora_status từ ChildProject (dùng fallback UI cũ)."""
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
async def heartbeat(
    x_worker_id: str = Header(default="default", alias="X-Worker-Id"),
) -> dict:
    """Extension gọi mỗi 10s để báo online. Mỗi Chrome profile có worker_id riêng."""
    _workers[x_worker_id] = time.time()
    await _broadcast_presence()
    return {"ok": True, "worker_count": len(_active_workers())}


@router.get("/next-job", dependencies=[Depends(_require_key)])
async def next_job(
    session: SessionDep,
    x_worker_id: str = Header(default="default", alias="X-Worker-Id"),
) -> dict:
    """Extension poll — trả về SoraJob pending tiếp theo (nếu có).
    Dùng lock để tránh 2 workers cùng claim 1 job."""
    _workers[x_worker_id] = time.time()  # poll = heartbeat

    global _last_job_started_at

    async with _next_job_lock:
        # ── Anti-ban: enforce minimum delay between job starts ─────────────
        delay = _sora_config.get("thread_start_delay", 0)
        if delay > 0:
            elapsed = time.time() - _last_job_started_at
            if elapsed < delay:
                return {"job": None, "reason": "throttle", "wait_s": round(delay - elapsed)}

        # ── Check max concurrent threads ───────────────────────────────────
        max_threads = _sora_config.get("max_threads", 1)
        gen_count_result = await session.execute(
            select(SoraJob).where(SoraJob.status == "generating")
        )
        generating_count = len(gen_count_result.scalars().all())
        if generating_count >= max_threads:
            return {"job": None, "reason": "max_threads", "generating": generating_count, "max": max_threads}

        result = await session.execute(
            select(SoraJob)
            .where(SoraJob.status == "pending")
            .order_by(SoraJob.created_at.asc(), SoraJob.job_index.asc())
            .limit(1)
        )
        job = result.scalar_one_or_none()

        if not job:
            return {"job": None}

        # Atomically mark as generating trước khi release lock
        job.status = "generating"
        job.progress = 0
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
        await session.refresh(job)
        _last_job_started_at = time.time()

    logger.info(
        "Sora next-job → job_id={} (index={}) assigned to worker={}",
        job.id, job.job_index, x_worker_id,
    )
    await _broadcast_job_patch(job)
    return {
        "job": {
            "id":       job.id,
            "index":    job.job_index,
            "prompt":   job.prompt,
            "ratio":    job.ratio or "16:9",
            "duration": job.duration or 5,
        }
    }


@router.post("/progress", dependencies=[Depends(_require_key)])
async def report_progress(body: "ProgressRequest", session: SessionDep) -> dict:
    """Extension reports approximate generation progress."""
    job = await session.get(SoraJob, body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sora job not found.")

    job.progress = max(0, min(100, body.progress))
    job.updated_at = datetime.utcnow()
    session.add(job)
    await session.commit()
    await _broadcast_job_patch(job)
    return {"ok": True}


@router.post("/job-done", dependencies=[Depends(_require_key)])
async def job_done(body: "JobDoneRequest", session: SessionDep) -> dict:
    """Extension reports a finished job and backend downloads the final video."""
    job = await session.get(SoraJob, body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sora job not found.")

    # Lấy child project để biết base_folder_path
    child = await session.get(ChildProject, job.child_project_id)

    if body.error:
        await _append_internal_job_log(body.job_id, "error", f"Job failed: {body.error}")
        job.status = "failed"
        job.progress = 0
        job.error_msg = body.error
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
        await _broadcast_job_patch(job)
        logger.warning("Sora job failed for job_id={}: {}", body.job_id, body.error)
        return {"ok": True, "status": "failed"}

    # Xác định folder lưu video
    if child and child.base_folder_path:
        dest_folder = Path(child.base_folder_path)
    else:
        dest_folder = Path.home() / "Downloads" / "sora-videos"

    # Download video
    try:
        video_url = body.no_watermark_url or body.downloadable_url
        if not video_url:
            raise ValueError("No downloadable URL provided.")
        raw_folder = Path(tempfile.mkdtemp(prefix="sora_raw_"))
        raw_path = await download_video(video_url, raw_folder, output_index=job.job_index)
        final_path = next_video_path(dest_folder, output_index=job.job_index, suffix=raw_path.suffix)

        await _append_internal_job_log(body.job_id, "success", "Remove watermark complete")
        await _append_internal_job_log(body.job_id, "info", "Denoise pass started")
        await _set_job_progress(session, job, 78)
        await _append_internal_job_log(body.job_id, "info", "Upscale 1080p started")
        await _set_job_progress(session, job, 90)
        saved_path = enhance_video_1080p(raw_path, ratio=job.ratio, destination_path=final_path)
        await _append_internal_job_log(body.job_id, "info", "Save to folder started")
        await _set_job_progress(session, job, 98)
        try:
            raw_path.unlink(missing_ok=True)
            raw_folder.rmdir()
        except Exception:
            pass

        job.status = "done"
        job.progress = 100
        job.video_path = str(saved_path)
        job.generation_id = body.generation_id or None
        job.permalink = body.public_permalink or None
        job.error_msg = None
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
        await _broadcast_job_patch(job)
        await _append_internal_job_log(body.job_id, "success", "1080p video saved to folder")

        logger.info("Sora job done for job_id={} → {}", body.job_id, saved_path)
        return {"ok": True, "status": "done", "video_path": str(saved_path)}

    except Exception as exc:
        await _append_internal_job_log(body.job_id, "error", f"Post-process failed: {exc}")
        job.status = "failed"
        job.error_msg = str(exc)
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
        await _broadcast_job_patch(job)
        logger.error("Sora download failed for job_id={}: {}", body.job_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/job-upload", dependencies=[Depends(_require_key)])
async def job_upload(
    session: SessionDep,
    job_id: int = Form(...),
    file: UploadFile = File(...),
    generation_id: str | None = Form(default=None),
    public_permalink: str | None = Form(default=None),
) -> dict:
    """Extension uploads the rendered MP4 blob directly when no stable URL exists."""
    job = await session.get(SoraJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sora job not found.")

    child = await session.get(ChildProject, job.child_project_id)
    dest_folder = Path(child.base_folder_path) if child and child.base_folder_path else Path.home() / "Downloads" / "sora-videos"

    try:
        content = await file.read()
        if not content:
            raise ValueError("Uploaded video is empty.")

        suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
        raw_folder = Path(tempfile.mkdtemp(prefix="sora_raw_upload_"))
        raw_path = save_uploaded_video(content, raw_folder, suffix=suffix, output_index=job.job_index)
        final_path = next_video_path(dest_folder, output_index=job.job_index, suffix=suffix)

        await _append_internal_job_log(job_id, "success", "Remove watermark complete")
        await _append_internal_job_log(job_id, "info", "Denoise pass started")
        await _set_job_progress(session, job, 78)
        await _append_internal_job_log(job_id, "info", "Upscale 1080p started")
        await _set_job_progress(session, job, 90)
        saved_path = enhance_video_1080p(raw_path, ratio=job.ratio, destination_path=final_path)
        await _append_internal_job_log(job_id, "info", "Save to folder started")
        await _set_job_progress(session, job, 98)
        try:
            raw_path.unlink(missing_ok=True)
            raw_folder.rmdir()
        except Exception:
            pass

        job.status = "done"
        job.progress = 100
        job.video_path = str(saved_path)
        job.generation_id = generation_id or None
        job.permalink = public_permalink or None
        job.error_msg = None
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
        await _broadcast_job_patch(job)
        await _append_internal_job_log(job_id, "success", "1080p video saved to folder")

        logger.info("Sora uploaded job done for job_id={} -> {}", job_id, saved_path)
        return {"ok": True, "status": "done", "video_path": str(saved_path)}
    except Exception as exc:
        await _append_internal_job_log(job_id, "error", f"Post-process failed: {exc}")
        job.status = "failed"
        job.error_msg = str(exc)
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.commit()
        await _broadcast_job_patch(job)
        logger.error("Sora upload failed for job_id={}: {}", job_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/requeue-failed/{child_project_id}")
async def requeue_failed(child_project_id: int, session: SessionDep) -> dict:
    """Frontend: reset tất cả failed jobs → pending để gen lại."""
    result = await session.execute(
        select(SoraJob)
        .where(SoraJob.child_project_id == child_project_id)
        .where(SoraJob.status == "failed")
    )
    jobs = result.scalars().all()
    for job in jobs:
        job.status = "pending"
        job.progress = 0
        job.error_msg = None
        job.updated_at = datetime.utcnow()
        session.add(job)
    await session.commit()
    logger.info("Sora requeue-failed: {} jobs reset for child_project_id={}", len(jobs), child_project_id)
    await _broadcast_jobs(session, child_project_id)
    await _broadcast_queue_hint(child_project_id)
    return {"ok": True, "requeued": len(jobs)}


class RequeueJobsRequest(BaseModel):
    job_ids: List[int]


@router.post("/requeue-jobs")
async def requeue_jobs(body: "RequeueJobsRequest", session: SessionDep) -> dict:
    """Frontend: reset các jobs được chọn (theo ID) → pending."""
    requeued = 0
    child_ids: set[int] = set()
    for job_id in body.job_ids:
        job = await session.get(SoraJob, job_id)
        if job:
            job.status = "pending"
            job.progress = 0
            job.error_msg = None
            job.updated_at = datetime.utcnow()
            session.add(job)
            requeued += 1
            child_ids.add(job.child_project_id)
    await session.commit()
    logger.info("Sora requeue-jobs: {} jobs reset", requeued)
    for child_id in child_ids:
        await _broadcast_jobs(session, child_id)
        await _broadcast_queue_hint(child_id)
    return {"ok": True, "requeued": requeued}


@router.delete("/job/{job_id}")
async def delete_single_job(job_id: int, session: SessionDep) -> dict:
    """Frontend: xóa một job cụ thể."""
    job = await session.get(SoraJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    child_project_id = job.child_project_id
    await session.delete(job)
    await session.commit()
    await _broadcast_jobs(session, child_project_id)
    return {"ok": True, "deleted": job_id}


@router.delete("/jobs/{child_project_id}")
async def clear_jobs(child_project_id: int, session: SessionDep) -> dict:
    """Frontend: xóa tất cả jobs của child project."""
    result = await session.execute(
        select(SoraJob).where(SoraJob.child_project_id == child_project_id)
    )
    jobs = result.scalars().all()
    for job in jobs:
        await session.delete(job)
    await session.commit()
    logger.info("Sora clear-jobs: {} jobs deleted for child_project_id={}", len(jobs), child_project_id)
    await _broadcast_jobs(session, child_project_id)
    return {"ok": True, "deleted": len(jobs)}


# ---------------------------------------------------------------------------
# Job log endpoints — extension streams step-by-step progress here
# ---------------------------------------------------------------------------

class JobLogRequest(BaseModel):
    job_id: int
    level: str    # "info" | "success" | "warn" | "error"
    message: str


@router.post("/job-log", dependencies=[Depends(_require_key)])
async def append_job_log(body: JobLogRequest) -> dict:
    """Extension posts each automation step here so frontend can show live log."""
    await _append_internal_job_log(body.job_id, body.level, body.message)
    return {"ok": True}


@router.websocket("/ws")
async def sora_realtime_ws(
    ws: WebSocket,
    client: str = "desktop",
    worker_id: str | None = None,
    api_key: str | None = None,
) -> None:
    if client == "worker":
        if not api_key or not validate_api_key(api_key):
            await ws.close(code=4401)
            return
        if not worker_id:
            await ws.close(code=4400)
            return

    await _realtime.connect(ws, client=client, worker_id=worker_id)
    if client == "worker" and worker_id:
        _workers[worker_id] = time.time()

    await _broadcast_presence()
    await ws.send_json({
        "type": "sora_config",
        "config": _sora_config.copy(),
    })

    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break

            if client == "worker" and worker_id:
                _workers[worker_id] = time.time()
                await _broadcast_presence()

            if raw == "ping":
                await ws.send_json({"type": "sora_pong"})
    except Exception as exc:
        logger.warning("Sora realtime WS error: {}", exc)
    finally:
        _realtime.disconnect(ws, client=client, worker_id=worker_id)
        await _broadcast_presence()


@router.get("/job-logs/{job_id}")
async def get_job_logs(job_id: int) -> dict:
    """Frontend polls this to display live automation log for a job."""
    return {"logs": _job_logs.get(job_id, [])}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class QueueBatchRequest(BaseModel):
    prompts: List[str]
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


class OutputFolderInspectRequest(BaseModel):
    folder: str


class OutputFolderInitRequest(BaseModel):
    folder: str
    conflict_mode: str = "keep_both"


class OutputFolderOpenRequest(BaseModel):
    folder: str
