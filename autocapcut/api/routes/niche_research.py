"""YouTube niche research routes backed by the official Data API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autocapcut.services.account_connect import (
    get_youtube_data_settings,
    save_youtube_data_settings,
)
from autocapcut.services.youtube_niche_research import (
    YouTubeNicheResearchError,
    research_youtube_niche,
    verify_youtube_api_key,
)


router = APIRouter()


class YouTubeDataSettingsModel(BaseModel):
    configured: bool = False
    connected: bool = False
    key_hint: str = ""
    verified_at: str = ""


class YouTubeDataSettingsResponse(BaseModel):
    ok: bool
    settings: YouTubeDataSettingsModel
    message: str


class YouTubeDataKeyVerifyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


class NicheResearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=160)
    region_code: str = Field(default="US", min_length=2, max_length=2)
    relevance_language: str = Field(default="en", min_length=2, max_length=12)
    published_within_days: int = Field(default=365, ge=7, le=3650)
    video_duration: Literal["any", "short", "medium", "long"] = "any"
    max_results: int = Field(default=25, ge=5, le=50)


def _settings_response(message: str) -> YouTubeDataSettingsResponse:
    settings = get_youtube_data_settings()
    api_key = str(settings.get("api_key") or "").strip()
    verified_at = str(settings.get("verified_at") or "").strip()
    return YouTubeDataSettingsResponse(
        ok=True,
        settings=YouTubeDataSettingsModel(
            configured=bool(api_key),
            connected=bool(api_key and verified_at),
            key_hint=f"••••{api_key[-4:]}" if api_key else "",
            verified_at=verified_at,
        ),
        message=message,
    )


@router.get("/settings", response_model=YouTubeDataSettingsResponse)
async def get_settings() -> YouTubeDataSettingsResponse:
    settings = get_youtube_data_settings()
    connected = bool(settings.get("api_key") and settings.get("verified_at"))
    message = (
        "YouTube Data API key is connected."
        if connected
        else "Add a YouTube Data API key to research niches."
    )
    return _settings_response(message)


@router.post("/settings/verify", response_model=YouTubeDataSettingsResponse)
async def verify_settings(req: YouTubeDataKeyVerifyRequest) -> YouTubeDataSettingsResponse:
    api_key = req.api_key.strip()
    try:
        await verify_youtube_api_key(api_key)
    except YouTubeNicheResearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_youtube_data_settings({
        "api_key": api_key,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    })
    return _settings_response("YouTube Data API key connected successfully.")


@router.post("/search")
async def search_niches(req: NicheResearchRequest) -> dict[str, Any]:
    settings = get_youtube_data_settings()
    api_key = str(settings.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Configure a YouTube Data API key in Settings first.",
        )

    try:
        return await research_youtube_niche(
            api_key=api_key,
            query=req.query,
            region_code=req.region_code,
            relevance_language=req.relevance_language,
            published_within_days=req.published_within_days,
            video_duration=req.video_duration,
            max_results=req.max_results,
        )
    except YouTubeNicheResearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
