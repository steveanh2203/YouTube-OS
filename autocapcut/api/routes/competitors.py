"""Competitor video CRUD + duplicate check."""
from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from autocapcut.database.connection import get_session
from autocapcut.database.models import CompetitorVideo

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _extract_video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None

    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    if "youtu.be" in host and path:
        return path.split("/")[0]
    if "youtube.com" in host:
        if path.startswith("shorts/"):
            return path.split("/", 1)[1].split("/")[0]
        query = parse_qs(parsed.query)
        video_ids = query.get("v")
        if video_ids:
            return video_ids[0].strip() or None
    return None


def _normalize_url(url: str) -> tuple[str, str | None]:
    raw = url.strip()
    video_id = _extract_video_id(raw)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}", video_id
    return raw, None


class CompetitorCreate(BaseModel):
    child_project_id: int | None = None
    parent_project_id: int | None = None
    url: str
    title: str
    thumbnail_url: str = ""
    channel: str = ""
    purpose: str = "reference"
    notes: str = ""


class CompetitorUpdate(BaseModel):
    title: str | None = None
    thumbnail_url: str | None = None
    channel: str | None = None
    purpose: str | None = None
    notes: str | None = None


@router.get("/")
async def list_competitors(
    session: SessionDep,
    child_project_id: int | None = None,
):
    stmt = select(CompetitorVideo).order_by(CompetitorVideo.created_at.desc())
    if child_project_id is not None:
        stmt = stmt.where(CompetitorVideo.child_project_id == child_project_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [row.model_dump() for row in rows]


@router.get("/check")
async def check_duplicate(
    url: Annotated[str, Query(min_length=3)],
    session: SessionDep,
):
    normalized_url, video_id = _normalize_url(url)
    duplicate = (
        await session.execute(
            select(CompetitorVideo).where(CompetitorVideo.normalized_url == normalized_url)
        )
    ).scalars().first()
    return {
        "ok": True,
        "normalized_url": normalized_url,
        "video_id": video_id,
        "duplicate": duplicate.model_dump() if duplicate else None,
    }


@router.post("/", status_code=201)
async def create_competitor(data: CompetitorCreate, session: SessionDep):
    normalized_url, video_id = _normalize_url(data.url)
    duplicate = (
        await session.execute(
            select(CompetitorVideo).where(CompetitorVideo.normalized_url == normalized_url)
        )
    ).scalars().first()
    if duplicate:
        raise HTTPException(status_code=409, detail={
            "message": "Competitor URL already exists",
            "duplicate": duplicate.model_dump(),
        })

    entry = CompetitorVideo(
        child_project_id=data.child_project_id,
        parent_project_id=data.parent_project_id,
        url=data.url.strip(),
        normalized_url=normalized_url,
        video_id=video_id,
        title=data.title.strip(),
        thumbnail_url=data.thumbnail_url.strip(),
        channel=data.channel.strip(),
        purpose=data.purpose.strip() or "reference",
        notes=data.notes.strip(),
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry.model_dump()


@router.delete("/{competitor_id}", status_code=204)
async def delete_competitor(competitor_id: int, session: SessionDep):
    competitor = await session.get(CompetitorVideo, competitor_id)
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")
    await session.delete(competitor)
    await session.commit()


@router.patch("/{competitor_id}")
async def update_competitor(
    competitor_id: int,
    data: CompetitorUpdate,
    session: SessionDep,
):
    competitor = await session.get(CompetitorVideo, competitor_id)
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")

    patch = data.model_dump(exclude_unset=True)
    for key, value in patch.items():
        if value is None:
            continue
        setattr(competitor, key, value.strip() if isinstance(value, str) else value)

    session.add(competitor)
    await session.commit()
    await session.refresh(competitor)
    return competitor.model_dump()
