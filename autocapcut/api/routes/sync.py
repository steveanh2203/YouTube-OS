"""Sync routes — audio, images, captions."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from autocapcut.models import ProjectItem, ProjectSource, ProjectStatus
from autocapcut.services.sync_audio import SyncAudioError, sync_project_audio
from autocapcut.services.sync_images import SyncImageError, sync_project_images
from autocapcut.services.sync_captions import SyncCaptionError, sync_project_captions

router = APIRouter()


# ─── Request / Response models ────────────────────────────────────────────────

class SyncRequest(BaseModel):
    paths: List[str]


class SyncResult(BaseModel):
    path: str
    name: str
    ok: bool
    message: str


class SyncResponse(BaseModel):
    results: List[SyncResult]
    success: int
    failed: int


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_item(path: str) -> ProjectItem:
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {path}")
    return ProjectItem(
        name=p.name,
        path=str(p),
        source=ProjectSource.local,
        status=ProjectStatus.pending,
    )


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/audio", response_model=SyncResponse)
async def sync_audio(req: SyncRequest) -> SyncResponse:
    loop = asyncio.get_event_loop()

    async def _one(path: str) -> SyncResult:
        item = _make_item(path)
        try:
            summary = await loop.run_in_executor(None, lambda: sync_project_audio(item, make_backup=True))
            return SyncResult(
                path=path, name=item.name, ok=True,
                message=f"Updated {summary.updated_segments} segments · audio {summary.audio_duration // 1_000_000:.1f}s",
            )
        except SyncAudioError as exc:
            return SyncResult(path=path, name=item.name, ok=False, message=str(exc))
        except Exception as exc:
            return SyncResult(path=path, name=item.name, ok=False, message=f"Unexpected: {exc}")

    results = list(await asyncio.gather(*[_one(p) for p in req.paths]))
    ok = [r for r in results if r.ok]
    return SyncResponse(results=results, success=len(ok), failed=len(results) - len(ok))


@router.post("/images", response_model=SyncResponse)
async def sync_images(req: SyncRequest) -> SyncResponse:
    loop = asyncio.get_event_loop()

    async def _one(path: str) -> SyncResult:
        item = _make_item(path)
        try:
            summary = await loop.run_in_executor(None, lambda: sync_project_images(item))
            unmatched = len(summary.unmatched_audio) + len(summary.unmatched_images)
            return SyncResult(
                path=path, name=item.name, ok=True,
                message=f"Paired {summary.paired} · unmatched {unmatched}",
            )
        except SyncImageError as exc:
            return SyncResult(path=path, name=item.name, ok=False, message=str(exc))
        except Exception as exc:
            return SyncResult(path=path, name=item.name, ok=False, message=f"Unexpected: {exc}")

    results = list(await asyncio.gather(*[_one(p) for p in req.paths]))
    ok = [r for r in results if r.ok]
    return SyncResponse(results=results, success=len(ok), failed=len(results) - len(ok))


@router.post("/captions", response_model=SyncResponse)
async def sync_captions(req: SyncRequest) -> SyncResponse:
    loop = asyncio.get_event_loop()

    async def _one(path: str) -> SyncResult:
        item = _make_item(path)
        try:
            summary = await loop.run_in_executor(None, lambda: sync_project_captions(item))
            return SyncResult(
                path=path, name=item.name, ok=True,
                message=f"Paired {summary.paired}/{summary.caption_total} captions · {summary.image_total} images",
            )
        except SyncCaptionError as exc:
            return SyncResult(path=path, name=item.name, ok=False, message=str(exc))
        except Exception as exc:
            return SyncResult(path=path, name=item.name, ok=False, message=f"Unexpected: {exc}")

    results = list(await asyncio.gather(*[_one(p) for p in req.paths]))
    ok = [r for r in results if r.ok]
    return SyncResponse(results=results, success=len(ok), failed=len(results) - len(ok))

