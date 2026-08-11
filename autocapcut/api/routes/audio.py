from __future__ import annotations

import asyncio
import base64
import binascii
from io import BytesIO
from pathlib import Path
from typing import Literal
import zipfile

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from autocapcut.services.minimax_audio import (
    MiniMaxAudioError,
    list_voices,
    minimax_configured,
    synthesize_text,
)


router = APIRouter()


class MiniMaxVoiceOut(BaseModel):
    voice_id: str
    voice_name: str
    voice_type: str
    created_time: str | None = None


class MiniMaxHealthOut(BaseModel):
    configured: bool
    provider: Literal["minimax"] = "minimax"


class MiniMaxTtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    voice_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0.1, le=10.0)
    pitch: int = Field(default=0, ge=-12, le=12)
    emotion: str | None = Field(default=None, max_length=64)


class AudioZipItem(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)


def _decode_audio_payload(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"Invalid audio payload: {exc}") from exc


def _sanitize_archive_name(filename: str) -> str:
    clean = Path(filename).name.strip()
    if not clean:
        raise ValueError("Invalid archive filename.")
    return clean


def _dedupe_archive_name(filename: str, used_names: set[str]) -> str:
    if filename not in used_names:
        used_names.add(filename)
        return filename

    path = Path(filename)
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = f"{stem}_{index}{suffix}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        index += 1


def _build_zip_bytes(items: list[AudioZipItem]) -> bytes:
    output = BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in items:
            archive_name = _dedupe_archive_name(_sanitize_archive_name(item.filename), used_names)
            archive.writestr(archive_name, _decode_audio_payload(item.content_base64))
    return output.getvalue()


@router.get("/minimax/health", response_model=MiniMaxHealthOut)
async def get_minimax_health(
    x_minimax_api_key: str | None = Header(default=None),
) -> MiniMaxHealthOut:
    return MiniMaxHealthOut(configured=minimax_configured(api_key_override=x_minimax_api_key))


@router.get("/minimax/voices", response_model=list[MiniMaxVoiceOut])
async def get_minimax_voices(
    x_minimax_api_key: str | None = Header(default=None),
) -> list[MiniMaxVoiceOut]:
    try:
        voices = await list_voices(api_key_override=x_minimax_api_key)
        return [MiniMaxVoiceOut.model_validate(v.__dict__) for v in voices]
    except MiniMaxAudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/minimax/tts")
async def post_minimax_tts(
    payload: MiniMaxTtsRequest,
    x_minimax_api_key: str | None = Header(default=None),
) -> Response:
    try:
        audio_bytes = await synthesize_text(
            text=payload.text,
            voice_id=payload.voice_id,
            model=payload.model,
            speed=payload.speed,
            volume=payload.volume,
            pitch=payload.pitch,
            emotion=payload.emotion,
            api_key_override=x_minimax_api_key,
        )
    except MiniMaxAudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"Content-Disposition": 'inline; filename="minimax_tts.mp3"'},
    )


@router.post("/zip")
async def create_audio_zip(files: list[AudioZipItem]) -> Response:
    try:
        archive = await asyncio.to_thread(_build_zip_bytes, files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ai-audio.zip"'},
    )
