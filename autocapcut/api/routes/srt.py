"""SRT Generator route."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from autocapcut.api.routes.media import resolve_media_reference
from autocapcut.database.connection import get_session
from autocapcut.services.srt_generator import generate_merged_srt, SRTGeneratorError

router = APIRouter()


class SRTRequest(BaseModel):
    srt_path: str      # uploaded transcript/subtitle file
    content_text: str  # raw content/script text to align


class SRTResponse(BaseModel):
    ok: bool
    srt_output: str    # merged SRT content as string
    message: str


@router.post("/generate", response_model=SRTResponse)
async def generate_srt(
    req: SRTRequest,
    session: AsyncSession = Depends(get_session),
) -> SRTResponse:
    srt_path = await resolve_media_reference(req.srt_path, session) if req.srt_path.startswith("media:") else Path(req.srt_path)
    if not srt_path.exists():
        raise HTTPException(status_code=404, detail=f"SRT file not found: {req.srt_path}")

    loop = asyncio.get_event_loop()
    try:
        output = await loop.run_in_executor(
            None, lambda: generate_merged_srt(srt_path, req.content_text)
        )
        return SRTResponse(ok=True, srt_output=output, message="SRT generated successfully")
    except SRTGeneratorError as exc:
        return SRTResponse(ok=False, srt_output="", message=str(exc))
    except Exception as exc:
        return SRTResponse(ok=False, srt_output="", message=f"Unexpected error: {exc}")
