"""Raw SEO route — apply ExifTool metadata to video files."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from autocapcut.api.routes.media import resolve_media_reference
from autocapcut.database.connection import get_session
from autocapcut.services.raw_seo import apply_raw_seo, parse_keywords, RawSEOError
from autocapcut.services.media_workspace import resolve_workspace_file_reference, resolve_workspace_or_local

router = APIRouter()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


class SEORequest(BaseModel):
    folder_path: str | None = None
    video_path: str | None = None
    title: str
    description: str
    keywords_raw: str        # comma/newline separated
    rename_to_title: bool = False


class SEOFileResult(BaseModel):
    file: str
    ok: bool
    message: str


class SEOResponse(BaseModel):
    ok: bool
    processed: int
    succeeded: int
    failed: int
    results: List[SEOFileResult]
    message: str


def _collect_video_files(folder: Path) -> List[Path]:
    files: List[Path] = []
    for root, _, filenames in os.walk(folder):
        for name in filenames:
            if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                files.append(Path(root) / name)
    return files


@router.post("/apply", response_model=SEOResponse)
async def apply_seo(req: SEORequest, session: AsyncSession = Depends(get_session)) -> SEOResponse:
    if req.video_path:
        if req.video_path.startswith("media:"):
            video_path = await resolve_media_reference(req.video_path, session)
        elif req.video_path.startswith("workspace-file:"):
            video_path = resolve_workspace_file_reference(req.video_path)
        else:
            video_path = Path(req.video_path).expanduser().resolve()
        files = [video_path] if video_path.is_file() and video_path.suffix.lower() in VIDEO_EXTENSIONS else []
    elif req.folder_path:
        folder = resolve_workspace_or_local(req.folder_path)
        if not folder.exists():
            raise HTTPException(status_code=404, detail=f"Folder not found: {req.folder_path}")
        files = _collect_video_files(folder)
    else:
        raise HTTPException(status_code=422, detail="A video or workspace folder is required.")
    if not files:
        raise HTTPException(status_code=422, detail="No video files found in folder")

    keywords = parse_keywords(req.keywords_raw)

    loop = asyncio.get_event_loop()
    try:
        summary = await loop.run_in_executor(
            None,
            lambda: apply_raw_seo(
                files,
                title=req.title,
                description=req.description,
                keywords=keywords,
                rename_to_title=req.rename_to_title,
            ),
        )
        results = [
            SEOFileResult(file=str(r.file), ok=r.success, message=r.message)
            for r in summary.results
        ]
        return SEOResponse(
            ok=summary.failed == 0,
            processed=summary.processed,
            succeeded=summary.succeeded,
            failed=summary.failed,
            results=results,
            message=f"Applied SEO to {summary.succeeded}/{summary.processed} files",
        )
    except RawSEOError as exc:
        return SEOResponse(ok=False, processed=0, succeeded=0, failed=0, results=[], message=str(exc))
    except Exception as exc:
        return SEOResponse(ok=False, processed=0, succeeded=0, failed=0, results=[], message=f"Unexpected: {exc}")

