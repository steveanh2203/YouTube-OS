"""Audio Visualizer API routes."""
from __future__ import annotations

import asyncio
import json
import mimetypes
from urllib.parse import unquote, urlparse
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from autocapcut.database.connection import get_session
from autocapcut.database.models import VisualizerPreset
from autocapcut.api.routes.media import resolve_media_reference
from autocapcut.services.audio_visualizer import (
    VisualizerConfig,
    cancel_render,
    create_job,
    get_job,
    render_visualizer,
    validate_hex_color,
    validate_resolution,
)

router = APIRouter()
ALLOWED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}


def _normalize_local_path(raw: str) -> str:
    value = raw.strip()
    if value.startswith("file://"):
        parsed = urlparse(value)
        return unquote(parsed.path)
    return value


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RenderRequest(BaseModel):
    audio_path: str = Field(min_length=1)
    config: dict = Field(default_factory=dict)


class RenderResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress_pct: int
    output_path: Optional[str] = None
    download_url: Optional[str] = None
    error: Optional[str] = None


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: dict = Field(default_factory=dict)


class PresetOut(BaseModel):
    id: int
    name: str
    config: dict
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_config(raw: dict) -> VisualizerConfig:
    """Parse and validate a raw config dict. Raises HTTPException on error."""
    try:
        cfg = VisualizerConfig.from_dict(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config: {exc}")

    # Validate hex colors
    color_fields = [
        "visualizer_color", "background_solid_color",
        "background_gradient_color1", "background_gradient_color2",
        "text_color",
    ]
    for fname in color_fields:
        val = getattr(cfg, fname, None)
        if val and not validate_hex_color(val):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid hex color for {fname}: {val!r} — expected #RRGGBB",
            )

    # Validate custom resolution
    if cfg.resolution == "custom":
        w = cfg.resolution_custom_w or 1920
        h = cfg.resolution_custom_h or 1080
        err = validate_resolution(w, h)
        if err:
            raise HTTPException(status_code=422, detail={"error": "invalid_resolution", "message": err})

    return cfg


# ---------------------------------------------------------------------------
# Render endpoints
# ---------------------------------------------------------------------------

async def _resolve_input_path(value: str, session: AsyncSession) -> Path:
    if value.startswith("media:"):
        return await resolve_media_reference(value, session)
    return Path(_normalize_local_path(value))


@router.post("/render", response_model=RenderResponse, status_code=202)
async def start_render(
    req: RenderRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Start an async audio visualizer render job. Returns job_id immediately."""
    audio_p = await _resolve_input_path(req.audio_path, session)
    if not audio_p.exists():
        raise HTTPException(status_code=422, detail={"error": "file_not_found", "message": "Audio file not found."})

    size_mb = audio_p.stat().st_size / (1024 * 1024)
    if size_mb > 500:
        raise HTTPException(status_code=413, detail={"error": "file_too_large", "limit_mb": 500})

    cfg = _validate_config(req.config)
    if cfg.background_image_path:
        cfg.background_image_path = str(await _resolve_input_path(cfg.background_image_path, session))
    if cfg.cover_art_path:
        cfg.cover_art_path = str(await _resolve_input_path(cfg.cover_art_path, session))
    job_id = create_job()

    background_tasks.add_task(render_visualizer, job_id, str(audio_p), cfg)
    return RenderResponse(job_id=job_id)


@router.get("/preview-source")
async def preview_source(
    audio_path: str = Query(min_length=1),
    session: AsyncSession = Depends(get_session),
):
    """Stream selected local audio for frontend preview analyser."""
    audio_p = await _resolve_input_path(audio_path, session)
    if not audio_p.exists() or not audio_p.is_file():
        raise HTTPException(status_code=404, detail={"error": "file_not_found"})
    if audio_p.suffix.lower() not in ALLOWED_AUDIO_EXTS:
        raise HTTPException(status_code=422, detail={"error": "invalid_audio_extension"})

    media_type = mimetypes.guess_type(str(audio_p))[0] or "audio/mpeg"
    return FileResponse(
        path=str(audio_p),
        media_type=media_type,
        filename=audio_p.name,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/render/{job_id}", response_model=JobStatusResponse)
async def get_render_status(job_id: str):
    """Poll render job status. Returns 'lost' status if job not found (e.g., after restart)."""
    job = get_job(job_id)
    payload = job.to_dict()
    payload["download_url"] = f"/api/audio-visualizer/render/{job_id}/download" if job.status == "done" else None
    return JobStatusResponse(**payload)


@router.get("/render/{job_id}/download")
async def download_render(job_id: str):
    job = get_job(job_id)
    if job.status != "done" or not job.output_path:
        raise HTTPException(status_code=404, detail={"error": "render_not_ready"})
    output = Path(job.output_path)
    if not output.is_file():
        raise HTTPException(status_code=404, detail={"error": "output_missing"})
    return FileResponse(path=output, filename=output.name, media_type=mimetypes.guess_type(output.name)[0])


@router.post("/render/{job_id}/cancel")
async def cancel_render_job(job_id: str):
    """Cancel a running render job and clean up partial output."""
    ok = await cancel_render(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    return {"job_id": job_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Preset endpoints
# ---------------------------------------------------------------------------

@router.get("/presets", response_model=list[PresetOut])
async def list_presets(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(VisualizerPreset).order_by(VisualizerPreset.name))
    presets = result.scalars().all()
    return [
        PresetOut(
            id=p.id,  # type: ignore[arg-type]
            name=p.name,
            config=json.loads(p.config),
            created_at=str(p.created_at),
            updated_at=str(p.updated_at),
        )
        for p in presets
    ]


@router.post("/presets", response_model=PresetOut, status_code=201)
async def save_preset(body: PresetIn, session: AsyncSession = Depends(get_session)):
    # Validate config first
    _validate_config(body.config)

    # Check duplicate name
    existing = (await session.execute(
        select(VisualizerPreset).where(VisualizerPreset.name == body.name)
    )).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail={"error": "duplicate_name", "message": f"Preset '{body.name}' already exists"})

    now = datetime.utcnow()
    preset = VisualizerPreset(
        name=body.name,
        config=json.dumps(body.config),
        created_at=now,
        updated_at=now,
    )
    session.add(preset)
    await session.commit()
    await session.refresh(preset)

    return PresetOut(
        id=preset.id,  # type: ignore[arg-type]
        name=preset.name,
        config=json.loads(preset.config),
        created_at=str(preset.created_at),
        updated_at=str(preset.updated_at),
    )


@router.put("/presets/{preset_id}", response_model=PresetOut)
async def update_preset(preset_id: int, body: PresetIn, session: AsyncSession = Depends(get_session)):
    preset = await session.get(VisualizerPreset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail={"error": "preset_not_found"})

    _validate_config(body.config)

    preset.name = body.name
    preset.config = json.dumps(body.config)
    preset.updated_at = datetime.utcnow()
    session.add(preset)
    await session.commit()
    await session.refresh(preset)

    return PresetOut(
        id=preset.id,  # type: ignore[arg-type]
        name=preset.name,
        config=json.loads(preset.config),
        created_at=str(preset.created_at),
        updated_at=str(preset.updated_at),
    )


@router.delete("/presets/{preset_id}", status_code=204)
async def delete_preset(preset_id: int, session: AsyncSession = Depends(get_session)):
    preset = await session.get(VisualizerPreset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail={"error": "preset_not_found"})
    await session.delete(preset)
    await session.commit()
