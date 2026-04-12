"""FastAPI router for Tool Cut Automate (cut_automate service)."""
from __future__ import annotations

import threading as th
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from uuid import uuid4

from autocapcut.services import cut_automate as svc

router = APIRouter()

# ─── Pydantic models ──────────────────────────────────────────────────────────

class CatStockRequest(BaseModel):
    input_dir: str
    output_dir: str
    segment_time: int = 10

class EditRequest(BaseModel):
    input_dir: str
    output_dir: str
    bg_wav: str | None = None

class TaChangRequest(BaseModel):
    input_dir: str
    output_dir: str
    fps_fraction: str = "1/6"

class TachMp3Request(BaseModel):
    input_dir: str
    output_dir: str

class GopLeRequest(BaseModel):
    input_dir: str
    output_dir: str
    output_name: str = "video_gop_le.mp4"

class GopChanRequest(BaseModel):
    input_dir: str
    output_dir: str
    output_name: str = "video_gop_chan.mp4"

class ScanProjectRequest(BaseModel):
    project_dir: str

class InitProjectRequest(BaseModel):
    project_dir: str
    create_if_missing: bool = True

class ResetRequest(BaseModel):
    project_dir: str
    dirs: list[str] | None = None

class XoaPhotoRequest(BaseModel):
    image_dir: str
    dry_run: bool = False

class GopPhotoRandomRequest(BaseModel):
    image_dir: str
    output_dir: str
    fps: int = 1
    output_name: str = "video_random.mp4"

class GopVideoStockRandomRequest(BaseModel):
    input_dir: str
    output_dir: str
    output_name: str = "gop_stock_random.mp4"

class TaskResponse(BaseModel):
    ok: bool
    message: str
    data: Any = None


class StartJobRequest(BaseModel):
    step: str
    payload: dict[str, Any]


class JobResponse(BaseModel):
    ok: bool
    message: str
    data: Any = None


@dataclass
class CutAutomateJob:
    id: str
    step: str
    payload: dict[str, Any]
    status: str = "running"
    message: str = "Running..."
    result: Any = None
    started_at: int = field(default_factory=lambda: int(time.time() * 1000))
    finished_at: int | None = None
    stop_requested: bool = False
    stop_event: th.Event = field(default_factory=th.Event)
    lock: th.Lock = field(default_factory=th.Lock)


_JOBS: dict[str, CutAutomateJob] = {}
_JOBS_LOCK = th.Lock()


def _serialize_job(job: CutAutomateJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "step": job.step,
        "status": job.status,
        "message": job.message,
        "result": job.result,
        "stop_requested": job.stop_requested,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _resolve_job_runner(step: str, payload: dict[str, Any]):
    if step == "01":
        return lambda stop_event: svc.catstock(
            str(payload["input_dir"]),
            str(payload["output_dir"]),
            int(payload.get("segment_time", 10)),
            stop_event=stop_event,
        )
    if step == "02":
        return lambda stop_event: svc.edit_video(
            str(payload["input_dir"]),
            str(payload["output_dir"]),
            payload.get("bg_wav"),
            stop_event=stop_event,
        )
    if step == "03":
        return lambda stop_event: svc.tachanh(
            str(payload["input_dir"]),
            str(payload["output_dir"]),
            str(payload.get("fps_fraction", "1/6")),
            stop_event=stop_event,
        )
    if step == "04":
        return lambda stop_event: svc.tachmp3(
            str(payload["input_dir"]),
            str(payload["output_dir"]),
            stop_event=stop_event,
        )
    if step == "05":
        return lambda stop_event: svc.gop_le(
            str(payload["input_dir"]),
            str(payload["output_dir"]),
            str(payload.get("output_name", "video_gop_le.mp4")),
            stop_event=stop_event,
        )
    if step == "06":
        return lambda stop_event: svc.gop_chan(
            str(payload["input_dir"]),
            str(payload["output_dir"]),
            str(payload.get("output_name", "video_gop_chan.mp4")),
            stop_event=stop_event,
        )
    if step == "07":
        return lambda stop_event: svc.reset(
            str(payload["project_dir"]),
            payload.get("dirs"),
            stop_event=stop_event,
        )
    if step == "08_le":
        return lambda stop_event: svc.xoa_photo_le(
            str(payload["image_dir"]),
            bool(payload.get("dry_run", False)),
            stop_event=stop_event,
        )
    if step == "08_chan":
        return lambda stop_event: svc.xoa_photo_chan(
            str(payload["image_dir"]),
            bool(payload.get("dry_run", False)),
            stop_event=stop_event,
        )
    if step == "10":
        return lambda stop_event: svc.gop_photo_random(
            str(payload["image_dir"]),
            str(payload["output_dir"]),
            int(payload.get("fps", 1)),
            str(payload.get("output_name", "video_random.mp4")),
            stop_event=stop_event,
        )
    if step == "11":
        return lambda stop_event: svc.gop_video_stock_random(
            str(payload["input_dir"]),
            str(payload["output_dir"]),
            str(payload.get("output_name", "gop_stock_random.mp4")),
            stop_event=stop_event,
        )
    raise HTTPException(status_code=422, detail=f"Unsupported cut-automate step: {step}")


def _run_job(job_id: str, runner) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return

    try:
        result = runner(job.stop_event)
        with job.lock:
            if job.stop_requested or job.stop_event.is_set():
                job.status = "cancelled"
                job.message = "Stopped by user."
                job.result = {"ok": False, "message": "Stopped by user.", "data": None}
            else:
                job.status = "done"
                job.message = "Completed successfully."
                job.result = {"ok": True, "message": "Completed successfully.", "data": result}
    except svc.CutAutomateStopped as exc:
        with job.lock:
            job.status = "cancelled"
            job.message = str(exc)
            job.result = {"ok": False, "message": str(exc), "data": None}
    except Exception as exc:
        with job.lock:
            job.status = "error"
            job.message = str(exc)
            job.result = {"ok": False, "message": str(exc), "data": None}
    finally:
        with job.lock:
            job.finished_at = int(time.time() * 1000)

# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/status")
def status() -> dict:
    return svc.get_status()


@router.post("/jobs/start", response_model=JobResponse)
def start_job(req: StartJobRequest) -> JobResponse:
    runner = _resolve_job_runner(req.step, req.payload)
    job = CutAutomateJob(id=uuid4().hex[:12], step=req.step, payload=req.payload)
    with _JOBS_LOCK:
        _JOBS[job.id] = job
    thread = th.Thread(target=_run_job, args=(job.id, runner), daemon=True)
    thread.start()
    return JobResponse(ok=True, message="Job started.", data=_serialize_job(job))


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str) -> JobResponse:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(ok=True, message=job.message, data=_serialize_job(job))


@router.post("/jobs/{job_id}/stop", response_model=JobResponse)
def stop_job(job_id: str) -> JobResponse:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    with job.lock:
        job.stop_requested = True
        if job.status == "running":
            job.message = "Stopping..."
            job.stop_event.set()
    return JobResponse(ok=True, message="Stop requested.", data=_serialize_job(job))


@router.post("/catstock", response_model=TaskResponse)
def catstock(req: CatStockRequest) -> TaskResponse:
    try:
        results = svc.catstock(req.input_dir, req.output_dir, req.segment_time)
        return TaskResponse(ok=True, message=f"Cut into {len(results)} segment(s).", data=results)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/edit", response_model=TaskResponse)
def edit_video(req: EditRequest) -> TaskResponse:
    try:
        results = svc.edit_video(req.input_dir, req.output_dir, req.bg_wav)
        return TaskResponse(ok=True, message=f"Rendered {len(results)} video(s).", data=results)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/tachanh", response_model=TaskResponse)
def tachanh(req: TaChangRequest) -> TaskResponse:
    try:
        results = svc.tachanh(req.input_dir, req.output_dir, req.fps_fraction)
        return TaskResponse(ok=True, message=f"Extracted {len(results)} frame(s).", data=results)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/tachmp3", response_model=TaskResponse)
def tachmp3(req: TachMp3Request) -> TaskResponse:
    try:
        results = svc.tachmp3(req.input_dir, req.output_dir)
        return TaskResponse(ok=True, message=f"Extracted {len(results)} MP3 file(s).", data=results)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/gop-le", response_model=TaskResponse)
def gop_le(req: GopLeRequest) -> TaskResponse:
    try:
        out = svc.gop_le(req.input_dir, req.output_dir, req.output_name)
        return TaskResponse(ok=True, message="Odd-numbered clips merged successfully.", data=out)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/gop-chan", response_model=TaskResponse)
def gop_chan(req: GopChanRequest) -> TaskResponse:
    try:
        out = svc.gop_chan(req.input_dir, req.output_dir, req.output_name)
        return TaskResponse(ok=True, message="Even-numbered clips merged successfully.", data=out)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/reset", response_model=TaskResponse)
def reset(req: ResetRequest) -> TaskResponse:
    try:
        deleted = svc.reset(req.project_dir, req.dirs)
        return TaskResponse(ok=True, message=f"Reset complete. Deleted {len(deleted)} file(s).", data=deleted)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/xoa-photo-le", response_model=TaskResponse)
def xoa_photo_le(req: XoaPhotoRequest) -> TaskResponse:
    try:
        affected = svc.xoa_photo_le(req.image_dir, req.dry_run)
        verb = "Previewed" if req.dry_run else "Deleted"
        return TaskResponse(ok=True, message=f"{verb} {len(affected)} odd-numbered image(s).", data=affected)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/xoa-photo-chan", response_model=TaskResponse)
def xoa_photo_chan(req: XoaPhotoRequest) -> TaskResponse:
    try:
        affected = svc.xoa_photo_chan(req.image_dir, req.dry_run)
        verb = "Previewed" if req.dry_run else "Deleted"
        return TaskResponse(ok=True, message=f"{verb} {len(affected)} even-numbered image(s).", data=affected)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/gop-photo-random", response_model=TaskResponse)
def gop_photo_random(req: GopPhotoRandomRequest) -> TaskResponse:
    try:
        out = svc.gop_photo_random(req.image_dir, req.output_dir, req.fps, req.output_name)
        return TaskResponse(ok=True, message="Image-to-video merge completed.", data=out)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/gop-video-stock-random", response_model=TaskResponse)
def gop_video_stock_random(req: GopVideoStockRandomRequest) -> TaskResponse:
    try:
        out = svc.gop_video_stock_random(req.input_dir, req.output_dir, req.output_name)
        return TaskResponse(ok=True, message="Stock video merge completed.", data=out)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/scan-project", response_model=TaskResponse)
def scan_project(req: ScanProjectRequest) -> TaskResponse:
    try:
        result = svc.scan_project(req.project_dir)
        status = "complete" if result["is_complete"] else f"missing {len(result['missing'])} folders"
        return TaskResponse(ok=True, message=f"Scan complete — structure is {status}.", data=result)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))


@router.post("/init-project", response_model=TaskResponse)
def init_project(req: InitProjectRequest) -> TaskResponse:
    try:
        result = svc.init_project(req.project_dir, req.create_if_missing)
        created = len(result["created"])
        existed = len(result["already_existed"])
        msg = f"Initialization complete — created {created} folder(s), {existed} already existed."
        return TaskResponse(ok=True, message=msg, data=result)
    except Exception as e:
        return TaskResponse(ok=False, message=str(e))
