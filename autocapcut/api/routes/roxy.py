"""Roxy Browser upload routes."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from autocapcut.api.routes.media import resolve_media_reference
from autocapcut.database.connection import get_session
from autocapcut.services.media_workspace import (
    MediaWorkspaceError,
    resolve_workspace_file_reference,
    resolve_workspace_or_local,
    workspace_file_reference,
)
from autocapcut.services.roxy_upload import (
    RoxyApiClient,
    RoxyUploadError,
    upload_video_via_roxy,
)

router = APIRouter()


# ─── Models ───────────────────────────────────────────────────────────────────

class RoxyConnectRequest(BaseModel):
    api_host: str
    api_token: str


class WorkspaceInfo(BaseModel):
    workspace_id: int
    workspace_name: str


class ProfileInfo(BaseModel):
    dir_id: str
    display_name: str


class RoxyWorkspacesResponse(BaseModel):
    ok: bool
    workspaces: List[WorkspaceInfo]
    message: str


class RoxyProfilesRequest(BaseModel):
    api_host: str
    api_token: str
    workspace_id: int


class RoxyProfilesResponse(BaseModel):
    ok: bool
    profiles: List[ProfileInfo]
    message: str


class RoxyUploadRequest(BaseModel):
    api_host: str
    api_token: str
    workspace_id: int
    profile_id: str
    video_path: str
    schedule_at: str | None = None
    close_after: bool = False
    close_tab_after: bool = False
    check_wait_seconds: int | None = None


class RoxyUploadResponse(BaseModel):
    ok: bool
    message: str
    debugger_address: str = ""
    schedule_applied: bool = False
    scheduled_at: str | None = None


class RoxyFolderVideosRequest(BaseModel):
    folder_path: str


class RoxyFolderVideosResponse(BaseModel):
    ok: bool
    folder_path: str
    videos: List[str]
    message: str


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


def _normalize_schedule_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(microsecond=0)
    return value.astimezone().replace(tzinfo=None, microsecond=0)


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/workspaces", response_model=RoxyWorkspacesResponse)
async def list_workspaces(req: RoxyConnectRequest) -> RoxyWorkspacesResponse:
    loop = asyncio.get_event_loop()
    try:
        def _run():
            client = RoxyApiClient(req.api_host, req.api_token)
            return client.list_workspaces()

        workspaces = await loop.run_in_executor(None, _run)
        return RoxyWorkspacesResponse(
            ok=True,
            workspaces=[WorkspaceInfo(workspace_id=w.workspace_id, workspace_name=w.workspace_name) for w in workspaces],
            message=f"Found {len(workspaces)} workspace(s)",
        )
    except RoxyUploadError as exc:
        return RoxyWorkspacesResponse(ok=False, workspaces=[], message=str(exc))
    except Exception as exc:
        return RoxyWorkspacesResponse(ok=False, workspaces=[], message=f"Unexpected: {exc}")


@router.post("/profiles", response_model=RoxyProfilesResponse)
async def list_profiles(req: RoxyProfilesRequest) -> RoxyProfilesResponse:
    loop = asyncio.get_event_loop()
    try:
        def _run():
            client = RoxyApiClient(req.api_host, req.api_token)
            return client.list_profiles(req.workspace_id, page_size=200)

        profiles = await loop.run_in_executor(None, _run)
        return RoxyProfilesResponse(
            ok=True,
            profiles=[ProfileInfo(dir_id=p.dir_id, display_name=p.display_name) for p in profiles],
            message=f"Found {len(profiles)} profile(s)",
        )
    except RoxyUploadError as exc:
        return RoxyProfilesResponse(ok=False, profiles=[], message=str(exc))
    except Exception as exc:
        return RoxyProfilesResponse(ok=False, profiles=[], message=f"Unexpected: {exc}")


@router.post("/upload", response_model=RoxyUploadResponse)
async def start_upload(
    req: RoxyUploadRequest,
    session: AsyncSession = Depends(get_session),
) -> RoxyUploadResponse:
    try:
        if req.video_path.startswith("media:"):
            video_path = await resolve_media_reference(req.video_path, session)
        elif req.video_path.startswith("workspace-file:"):
            video_path = resolve_workspace_file_reference(req.video_path)
        else:
            video_path = Path(req.video_path).expanduser()
    except (MediaWorkspaceError, ValueError) as exc:
        return RoxyUploadResponse(ok=False, message=str(exc))
    if not video_path.exists():
        return RoxyUploadResponse(ok=False, message=f"Video file not found: {req.video_path}")

    schedule_at: datetime | None = None
    if req.schedule_at:
        try:
            schedule_at = _normalize_schedule_local(datetime.fromisoformat(req.schedule_at))
        except ValueError:
            return RoxyUploadResponse(ok=False, message="Schedule time is invalid.")

    loop = asyncio.get_event_loop()
    try:
        def _run():
            return upload_video_via_roxy(
                api_host=req.api_host,
                api_token=req.api_token,
                workspace_id=req.workspace_id,
                profile_id=req.profile_id,
                video_path=video_path,
                schedule_at=schedule_at,
                close_profile_after_start=req.close_after,
                close_upload_tab_after=req.close_tab_after,
                check_wait_seconds=req.check_wait_seconds,
            )

        summary = await loop.run_in_executor(None, _run)
        return RoxyUploadResponse(
            ok=True,
            message=summary.message,
            debugger_address=summary.debugger_address,
            schedule_applied=summary.schedule_applied,
            scheduled_at=summary.scheduled_at,
        )
    except RoxyUploadError as exc:
        return RoxyUploadResponse(ok=False, message=str(exc))
    except Exception as exc:
        return RoxyUploadResponse(ok=False, message=f"Unexpected: {exc}")


@router.post("/folder-videos", response_model=RoxyFolderVideosResponse)
async def list_folder_videos(req: RoxyFolderVideosRequest) -> RoxyFolderVideosResponse:
    try:
        folder = resolve_workspace_or_local(req.folder_path)
    except MediaWorkspaceError as exc:
        return RoxyFolderVideosResponse(ok=False, folder_path=req.folder_path, videos=[], message=str(exc))
    if not folder.exists():
        return RoxyFolderVideosResponse(
            ok=False,
            folder_path=req.folder_path,
            videos=[],
            message="Folder không tồn tại.",
        )
    if not folder.is_dir():
        return RoxyFolderVideosResponse(
            ok=False,
            folder_path=req.folder_path,
            videos=[],
            message="Đường dẫn đã chọn không phải folder.",
        )

    video_paths = sorted(
        [item for item in folder.iterdir() if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS],
        key=lambda item: item.name.lower(),
    )
    videos = [
        workspace_file_reference(req.folder_path, item) if req.folder_path.startswith("workspace:") else str(item)
        for item in video_paths
    ]
    return RoxyFolderVideosResponse(
        ok=True,
        folder_path=req.folder_path,
        videos=videos,
        message=f"Tìm thấy {len(videos)} video.",
    )
