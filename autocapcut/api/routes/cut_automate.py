"""FastAPI router for Tool Cut Automate (cut_automate service)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

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

# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/status")
def status() -> dict:
    return svc.get_status()


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
