"""SRT Generator route."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from autocapcut.services.srt_generator import generate_merged_srt, SRTGeneratorError

router = APIRouter()


class SRTRequest(BaseModel):
    srt_path: str      # path to CapCut-exported SRT file
    content_text: str  # raw content/script text to align


class SRTResponse(BaseModel):
    ok: bool
    srt_output: str    # merged SRT content as string
    message: str


class SRTSaveRequest(BaseModel):
    save_path: str   # absolute path to write
    content: str     # SRT text content


class SRTSaveResponse(BaseModel):
    ok: bool
    message: str


@router.post("/save", response_model=SRTSaveResponse)
async def save_srt(req: SRTSaveRequest) -> SRTSaveResponse:
    try:
        path = Path(req.save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: path.write_text(req.content, encoding="utf-8"))
        return SRTSaveResponse(ok=True, message=f"Saved to {path.name}")
    except Exception as exc:
        return SRTSaveResponse(ok=False, message=f"File write error: {exc}")


@router.post("/generate", response_model=SRTResponse)
async def generate_srt(req: SRTRequest) -> SRTResponse:
    srt_path = Path(req.srt_path)
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
