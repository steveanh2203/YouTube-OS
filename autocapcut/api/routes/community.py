"""Community post CRUD + queue + publish routes."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from autocapcut.database.connection import get_session
from autocapcut.database.models import CommunityPost, ParentProject
from autocapcut.services.community_posting import (
    CommunityPostingError,
    PublishRuntimeInput,
    UNSET,
    apply_post_payload,
    get_post_logs,
    publish_queued_post,
    queue_post,
    retry_post,
    should_publish_now,
)


router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CommunityPostOut(BaseModel):
    id: int
    parent_project_id: int
    child_project_id: int | None = None
    body: str
    image_path: str | None = None
    status: str
    post_mode: str
    schedule_at: datetime | None = None
    queued_at: datetime | None = None
    posting_started_at: datetime | None = None
    published_at: datetime | None = None
    youtube_post_url: str | None = None
    youtube_post_id: str | None = None
    channel_url: str | None = None
    last_error: str | None = None
    last_attempt_at: datetime | None = None
    attempt_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CommunityPostListResponse(BaseModel):
    ok: bool
    posts: list[CommunityPostOut]
    message: str


class CommunityPostMutationResponse(BaseModel):
    ok: bool
    post: CommunityPostOut | None = None
    message: str


class CommunityLogEntry(BaseModel):
    ts: str
    level: str
    message: str
    meta: dict | None = None


class CommunityLogResponse(BaseModel):
    ok: bool
    logs: list[CommunityLogEntry]
    message: str


class CommunityPostCreate(BaseModel):
    parent_project_id: int = Field(gt=0)
    child_project_id: int | None = None
    body: str = ""
    image_path: str | None = None
    channel_url: str | None = None
    post_mode: str = "now"
    schedule_at: datetime | None = None


class CommunityPostUpdate(BaseModel):
    body: str | None = None
    image_path: str | None = None
    child_project_id: int | None = None
    channel_url: str | None = None
    post_mode: str | None = None
    schedule_at: datetime | None = None


class CommunityPublishRequest(BaseModel):
    api_host: str = Field(min_length=1)
    api_token: str = Field(min_length=1)


class CommunityRetryRequest(BaseModel):
    force: bool = False


def _message_for_status(post: CommunityPost) -> str:
    if post.status == "published":
        return "Community post published."
    if post.status == "queued":
        return "Community post queued."
    if post.status == "failed":
        return post.last_error or "Community post failed."
    return f"Community post is {post.status}."


@router.get("/posts", response_model=CommunityPostListResponse)
async def list_posts(
    session: SessionDep,
    parent_project_id: Annotated[int, Query(gt=0)],
    status: str | None = None,
    q: str | None = None,
) -> CommunityPostListResponse:
    stmt = (
        select(CommunityPost)
        .where(CommunityPost.parent_project_id == parent_project_id)
        .order_by(CommunityPost.created_at.desc())
    )
    if status and status.strip():
        stmt = stmt.where(CommunityPost.status == status.strip())
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                CommunityPost.body.ilike(like),
                CommunityPost.channel_url.ilike(like),
            )
        )

    posts = (await session.execute(stmt)).scalars().all()
    return CommunityPostListResponse(
        ok=True,
        posts=[CommunityPostOut.model_validate(post) for post in posts],
        message=f"Loaded {len(posts)} community post(s).",
    )


@router.get("/due", response_model=CommunityPostListResponse)
async def list_due_posts(session: SessionDep) -> CommunityPostListResponse:
    now = datetime.utcnow()
    stmt = (
        select(CommunityPost)
        .where(CommunityPost.status == "queued")
        .order_by(CommunityPost.schedule_at.asc().nullsfirst(), CommunityPost.queued_at.asc().nullsfirst())
    )
    posts = (await session.execute(stmt)).scalars().all()
    due = [post for post in posts if should_publish_now(post, now)]
    return CommunityPostListResponse(
        ok=True,
        posts=[CommunityPostOut.model_validate(post) for post in due],
        message=f"Found {len(due)} due community post(s).",
    )


@router.post("/posts", response_model=CommunityPostMutationResponse)
async def create_post(body: CommunityPostCreate, session: SessionDep) -> CommunityPostMutationResponse:
    parent = await session.get(ParentProject, body.parent_project_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent project not found.")

    post = CommunityPost(parent_project_id=body.parent_project_id)
    apply_post_payload(
        post,
        body=body.body,
        image_path=body.image_path,
        child_project_id=body.child_project_id,
        channel_url=body.channel_url,
        post_mode=body.post_mode,
        schedule_at=body.schedule_at,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return CommunityPostMutationResponse(ok=True, post=CommunityPostOut.model_validate(post), message="Draft created.")


@router.patch("/posts/{post_id}", response_model=CommunityPostMutationResponse)
async def update_post(post_id: int, body: CommunityPostUpdate, session: SessionDep) -> CommunityPostMutationResponse:
    post = await session.get(CommunityPost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Community post not found.")
    if post.status in {"posting", "published"}:
        raise HTTPException(status_code=409, detail="This community post can no longer be edited.")

    patch = body.model_dump(exclude_unset=True)
    apply_post_payload(
        post,
        body=patch["body"] if "body" in patch else UNSET,
        image_path=patch["image_path"] if "image_path" in patch else UNSET,
        child_project_id=patch["child_project_id"] if "child_project_id" in patch else UNSET,
        channel_url=patch["channel_url"] if "channel_url" in patch else UNSET,
        post_mode=patch["post_mode"] if "post_mode" in patch else UNSET,
        schedule_at=patch["schedule_at"] if "schedule_at" in patch else UNSET,
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return CommunityPostMutationResponse(ok=True, post=CommunityPostOut.model_validate(post), message="Draft updated.")


@router.post("/posts/{post_id}/queue", response_model=CommunityPostMutationResponse)
async def queue_post_route(post_id: int, session: SessionDep) -> CommunityPostMutationResponse:
    post = await session.get(CommunityPost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Community post not found.")
    try:
        queue_post(post)
    except CommunityPostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.add(post)
    await session.commit()
    await session.refresh(post)
    return CommunityPostMutationResponse(ok=True, post=CommunityPostOut.model_validate(post), message="Post queued.")


@router.post("/posts/{post_id}/retry", response_model=CommunityPostMutationResponse)
async def retry_post_route(
    post_id: int,
    body: CommunityRetryRequest,
    session: SessionDep,
) -> CommunityPostMutationResponse:
    post = await session.get(CommunityPost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Community post not found.")
    try:
        retry_post(post, force=body.force)
    except CommunityPostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.add(post)
    await session.commit()
    await session.refresh(post)
    return CommunityPostMutationResponse(ok=True, post=CommunityPostOut.model_validate(post), message="Post queued for retry.")


@router.post("/posts/{post_id}/publish-now", response_model=CommunityPostMutationResponse)
async def publish_now_route(
    post_id: int,
    body: CommunityPublishRequest,
    session: SessionDep,
) -> CommunityPostMutationResponse:
    post = await session.get(CommunityPost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Community post not found.")
    try:
        queue_post(post)
    except CommunityPostingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.add(post)
    await session.commit()

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            publish_queued_post,
            post_id,
            PublishRuntimeInput(api_host=body.api_host, api_token=body.api_token),
        )
    except Exception as exc:
        logger_message = str(exc)
        await session.refresh(post)
        return CommunityPostMutationResponse(
            ok=False,
            post=CommunityPostOut.model_validate(post),
            message=logger_message,
        )

    await session.refresh(post)
    return CommunityPostMutationResponse(
        ok=True,
        post=CommunityPostOut.model_validate(post),
        message=_message_for_status(post),
    )


@router.get("/posts/{post_id}/logs", response_model=CommunityLogResponse)
async def get_logs(post_id: int) -> CommunityLogResponse:
    logs = [CommunityLogEntry(**entry) for entry in get_post_logs(post_id)]
    return CommunityLogResponse(ok=True, logs=logs, message=f"Loaded {len(logs)} log entries.")
