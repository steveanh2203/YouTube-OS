"""Raw SEO route — apply ExifTool metadata to video files."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from autocapcut.services.raw_seo import apply_raw_seo, parse_keywords, RawSEOError

router = APIRouter()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


class SEORequest(BaseModel):
    folder_path: str
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
async def apply_seo(req: SEORequest) -> SEOResponse:
    folder = Path(req.folder_path)
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {req.folder_path}")

    files = _collect_video_files(folder)
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

