"""Project discovery & inspection routes."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from autocapcut.services.project_loader import discover_projects, inspect_project
from autocapcut.models import ProjectItem, ProjectSource, ProjectStatus

router = APIRouter()


# ─── Response Models ─────────────────────────────────────────────────────────

class ProjectMetaOut(BaseModel):
    duration_s: float
    fps: float
    width: int | None
    height: int | None
    video_segments: int
    track_count: int
    ffmpeg_ready: bool
    modified_at: str | None


class CapcutProjectOut(BaseModel):
    id: str
    name: str
    path: str
    source: str          # 'local' | 'cloud_cache'
    status: str          # 'pending' | 'processing' | 'done' | 'failed'
    notes: str
    assigned_project_id: str | None
    is_selected: bool
    selection_order: int | None
    metadata: ProjectMetaOut


def _item_to_out(item: ProjectItem) -> CapcutProjectOut:
    meta = item.metadata or {}
    ffmpeg = meta.get("ffmpeg") or {}
    canvas = meta.get("canvas") or {}

    # Build a stable ID from path
    item_id = f"cc_{abs(hash(item.path))}"

    import datetime
    modified_ts = meta.get("modified_ts") or meta.get("fs_modified_ts")
    if modified_ts:
        try:
            modified_at = datetime.datetime.fromtimestamp(modified_ts).strftime("%Y-%m-%d")
        except Exception:
            modified_at = None
    else:
        modified_at = None

    return CapcutProjectOut(
        id=item_id,
        name=item.name,
        path=item.path,
        source=item.source.value if hasattr(item.source, "value") else str(item.source),
        status=item.status.name if hasattr(item.status, "name") else str(item.status),
        notes="",
        assigned_project_id=None,
        is_selected=item.is_selected,
        selection_order=item.selection_order,
        metadata=ProjectMetaOut(
            duration_s=round(meta.get("duration_s", 0), 2),
            fps=meta.get("fps", 30),
            width=canvas.get("width"),
            height=canvas.get("height"),
            video_segments=meta.get("video_segments", 0),
            track_count=meta.get("track_count", 0),
            ffmpeg_ready=ffmpeg.get("ready", False),
            modified_at=modified_at,
        ),
    )


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/discover", response_model=list[CapcutProjectOut])
async def discover(root: str | None = Query(default=None)) -> list[CapcutProjectOut]:
    """Scan CapCut project directories and return all found projects."""
    root_path = Path(root) if root else None
    loop = asyncio.get_event_loop()
    try:
        items = await loop.run_in_executor(None, lambda: discover_projects(root=root_path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Enrich each item in parallel — inspect_project reads files/JSON, CPU+IO bound
    async def _enrich(item: ProjectItem) -> CapcutProjectOut:
        try:
            detail = await loop.run_in_executor(None, lambda: inspect_project(item.path))
            item.metadata.update(detail)
        except Exception:
            pass
        return _item_to_out(item)

    enriched = await asyncio.gather(*[_enrich(item) for item in items])
    return list(enriched)


@router.get("/inspect", response_model=dict)
async def inspect(path: str = Query(...)) -> dict[str, Any]:
    """Return detailed metadata for a single CapCut project."""
    folder = Path(path)
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Project folder not found: {path}")
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, lambda: inspect_project(folder))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
