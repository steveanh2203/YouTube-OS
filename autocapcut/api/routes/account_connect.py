"""YTB Connect bridge routes for browser extension pairing."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from autocapcut.api.routes.extension import manager as extension_manager
from autocapcut.database.connection import get_session
from autocapcut.database.models import ParentProject
from autocapcut.services.account_connect import (
    disconnect_youtube_config,
    get_bridge_settings,
    get_oauth_settings,
    is_bridge_key_valid,
    oauth_settings_ready,
    record_youtube_quota_usage,
    regenerate_bridge_key,
    save_oauth_settings,
    save_youtube_inbox_cache,
    save_verified_connection,
)
from autocapcut.services.youtube_comments import (
    YouTubeCommentsError,
    exchange_oauth_code,
    verify_channel_access,
)

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class BridgeSettingsResponse(BaseModel):
    ok: bool
    bridge_url: str
    bridge_key: str
    extension_name: str
    extension_connected: bool = False
    extension_client_count: int = 0
    oauth_client_id: str = ""
    oauth_ready: bool = False
    message: str


class OAuthSettingsModel(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    verified_at: str = ""


class OAuthSettingsResponse(BaseModel):
    ok: bool
    settings: OAuthSettingsModel
    ready: bool
    message: str


class OAuthSettingsVerifyRequest(BaseModel):
    client_id: str = Field(default="", max_length=300)
    client_secret: str = Field(default="", max_length=300)


class OAuthExchangeRequest(BaseModel):
    parent_project_id: int
    code: str = Field(min_length=1, max_length=4000)
    redirect_uri: str = Field(min_length=1, max_length=500)


class BridgeParentProject(BaseModel):
    id: int
    name: str
    connected: bool = False
    channel_name: str = ""
    channel_id: str = ""


class BridgeParentProjectsResponse(BaseModel):
    ok: bool
    items: list[BridgeParentProject]


class BridgeHealthResponse(BaseModel):
    ok: bool
    app: str
    message: str


class BridgeConnectRequest(BaseModel):
    parent_project_id: int
    access_token: str = ""
    channel_id: str = Field(default="", max_length=120)
    channel_name: str = Field(default="", max_length=240)
    provider_mode: str = Field(default="api", max_length=40)
    session_cookie: str = ""
    session_index: str = Field(default="", max_length=40)
    delegated_session_id: str = Field(default="", max_length=240)
    visitor_data: str = Field(default="", max_length=240)


class BridgeConnectResponse(BaseModel):
    ok: bool
    message: str
    parent_project_id: int | None = None
    channel_id: str = ""
    channel_name: str = ""


class DisconnectRequest(BaseModel):
    parent_project_id: int


class BridgeInboxItem(BaseModel):
    thread_id: str
    comment_id: str
    video_id: str | None = None
    video_title: str | None = None
    author_display_name: str
    author_channel_id: str | None = None
    text: str
    published_at: str | None = None
    updated_at: str | None = None
    reply_count: int = 0
    is_from_channel_owner: bool = False
    can_auto_reply: bool = False


class BridgeInboxSyncRequest(BaseModel):
    parent_project_id: int
    items: list[BridgeInboxItem] = Field(default_factory=list)
    ok: bool = True
    message: str = ""
    synced_at: str = ""
    source: str = "extension"


class BridgeInboxSyncResponse(BaseModel):
    ok: bool
    message: str
    parent_project_id: int
    item_count: int


def _bridge_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _oauth_message(ready: bool) -> str:
    if ready:
        return "OAuth config looks ready. Qua extension login Gmail để pair kênh."
    return "Thiếu hoặc sai OAuth Client ID / Client Secret."


def _require_bridge_key(x_account_connect_key: str = "") -> None:
    if is_bridge_key_valid(x_account_connect_key):
        return
    raise HTTPException(status_code=401, detail="Invalid YTB Connect bridge key.")


@router.get("/settings", response_model=BridgeSettingsResponse)
async def get_settings(request: Request) -> BridgeSettingsResponse:
    settings = get_bridge_settings()
    oauth_settings = get_oauth_settings()
    return BridgeSettingsResponse(
        ok=True,
        bridge_url=_bridge_url(request),
        bridge_key=settings.bridge_key,
        extension_name=settings.extension_name,
        extension_connected=extension_manager.client_count > 0,
        extension_client_count=extension_manager.client_count,
        oauth_client_id=str(oauth_settings.get("client_id") or "").strip(),
        oauth_ready=oauth_settings_ready(oauth_settings),
        message="YTB Connect bridge is ready.",
    )


@router.post("/settings/regenerate", response_model=BridgeSettingsResponse)
async def regenerate_settings(request: Request) -> BridgeSettingsResponse:
    settings = regenerate_bridge_key()
    oauth_settings = get_oauth_settings()
    return BridgeSettingsResponse(
        ok=True,
        bridge_url=_bridge_url(request),
        bridge_key=settings.bridge_key,
        extension_name=settings.extension_name,
        extension_connected=extension_manager.client_count > 0,
        extension_client_count=extension_manager.client_count,
        oauth_client_id=str(oauth_settings.get("client_id") or "").strip(),
        oauth_ready=oauth_settings_ready(oauth_settings),
        message="Bridge key regenerated.",
    )


@router.get("/oauth-settings", response_model=OAuthSettingsResponse)
async def get_oauth_settings_route() -> OAuthSettingsResponse:
    settings = get_oauth_settings()
    ready = oauth_settings_ready(settings)
    return OAuthSettingsResponse(
        ok=True,
        settings=OAuthSettingsModel(**settings),
        ready=ready,
        message=_oauth_message(ready),
    )


@router.post("/oauth-settings/verify", response_model=OAuthSettingsResponse)
async def verify_oauth_settings(req: OAuthSettingsVerifyRequest) -> OAuthSettingsResponse:
    verified_at = datetime.now(timezone.utc).isoformat()
    settings = save_oauth_settings({
        "client_id": req.client_id,
        "client_secret": req.client_secret,
        "verified_at": verified_at,
    })
    ready = oauth_settings_ready(settings)
    if not ready:
        raise HTTPException(status_code=400, detail=_oauth_message(False))
    return OAuthSettingsResponse(
        ok=True,
        settings=OAuthSettingsModel(**settings),
        ready=True,
        message=_oauth_message(True),
    )


@router.get("/health", response_model=BridgeHealthResponse)
async def health(x_account_connect_key: str = Header(default="", alias="X-Account-Connect-Key")) -> BridgeHealthResponse:
    _require_bridge_key(x_account_connect_key)
    return BridgeHealthResponse(
        ok=True,
        app="MasterOS",
        message="YTB Connect bridge is reachable.",
    )


@router.get("/parent-projects", response_model=BridgeParentProjectsResponse)
async def list_bridge_parent_projects(
    session: SessionDep,
    x_account_connect_key: str = Header(default="", alias="X-Account-Connect-Key"),
) -> BridgeParentProjectsResponse:
    _require_bridge_key(x_account_connect_key)
    result = await session.execute(select(ParentProject).order_by(ParentProject.created_at.desc()))
    rows = result.scalars().all()

    from autocapcut.services.account_connect import list_youtube_configs

    configs = list_youtube_configs()
    items = [
        BridgeParentProject(
            id=int(project.id or 0),
            name=project.name,
            connected=bool(configs.get(str(project.id), {}).get("connected")),
            channel_name=str(configs.get(str(project.id), {}).get("channel_name") or ""),
            channel_id=str(configs.get(str(project.id), {}).get("channel_id") or ""),
        )
        for project in rows
        if project.id is not None
    ]
    return BridgeParentProjectsResponse(ok=True, items=items)


@router.post("/connect", response_model=BridgeConnectResponse)
async def connect_parent_project(
    req: BridgeConnectRequest,
    session: SessionDep,
    x_account_connect_key: str = Header(default="", alias="X-Account-Connect-Key"),
) -> BridgeConnectResponse:
    _require_bridge_key(x_account_connect_key)
    project = await session.get(ParentProject, req.parent_project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Parent project not found.")

    try:
        provider_mode = req.provider_mode.strip().lower() or "session"
        if provider_mode == "session":
            return BridgeConnectResponse(
                ok=False,
                message="Session mode da bi tat. Hãy reload extension và dùng OAuth login.",
                parent_project_id=req.parent_project_id,
            )
        verified_at = datetime.now(timezone.utc).isoformat()
        verified = await verify_channel_access(
            access_token=req.access_token,
            expected_channel_id=req.channel_id,
        )
        record_youtube_quota_usage("channel_verify", 1)
        channel_id = verified.channel_id
        channel_name = req.channel_name.strip() or verified.channel_title
        save_verified_connection(
            parent_id=req.parent_project_id,
            access_token=req.access_token,
            channel_id=channel_id,
            channel_name=channel_name,
            verified_at=verified_at,
            provider_mode="api",
        )
    except YouTubeCommentsError as exc:
        return BridgeConnectResponse(
            ok=False,
            message=str(exc),
            parent_project_id=req.parent_project_id,
        )

    await extension_manager.broadcast({
        "type": "extension_push",
        "feature": "account_connect",
        "content": json.dumps({
            "parent_project_id": req.parent_project_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "provider_mode": "api",
        }),
        "parent_project_id": req.parent_project_id,
        "child_project_id": None,
    })

    return BridgeConnectResponse(
        ok=True,
        message="Channel connected to parent project.",
        parent_project_id=req.parent_project_id,
        channel_id=channel_id,
        channel_name=channel_name,
    )


@router.post("/oauth/exchange", response_model=BridgeConnectResponse)
async def oauth_exchange(
    req: OAuthExchangeRequest,
    session: SessionDep,
    x_account_connect_key: str = Header(default="", alias="X-Account-Connect-Key"),
) -> BridgeConnectResponse:
    _require_bridge_key(x_account_connect_key)
    project = await session.get(ParentProject, req.parent_project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Parent project not found.")

    oauth_settings = get_oauth_settings()
    if not oauth_settings_ready(oauth_settings):
        raise HTTPException(status_code=400, detail="OAuth config chua san sang trong Settings.")

    try:
        token_bundle = await exchange_oauth_code(
            client_id=str(oauth_settings.get("client_id") or ""),
            client_secret=str(oauth_settings.get("client_secret") or ""),
            code=req.code,
            redirect_uri=req.redirect_uri,
        )
        verified = await verify_channel_access(access_token=token_bundle.access_token)
        record_youtube_quota_usage("channel_verify", 1)
    except YouTubeCommentsError as exc:
        return BridgeConnectResponse(
            ok=False,
            message=str(exc),
            parent_project_id=req.parent_project_id,
        )

    verified_at = datetime.now(timezone.utc).isoformat()
    save_verified_connection(
        parent_id=req.parent_project_id,
        access_token=token_bundle.access_token,
        refresh_token=token_bundle.refresh_token,
        token_expiry=token_bundle.expires_at,
        channel_id=verified.channel_id,
        channel_name=verified.channel_title,
        verified_at=verified_at,
        provider_mode="api",
    )

    await extension_manager.broadcast({
        "type": "extension_push",
        "feature": "account_connect",
        "content": json.dumps({
            "parent_project_id": req.parent_project_id,
            "channel_id": verified.channel_id,
            "channel_name": verified.channel_title,
            "provider_mode": "api",
        }),
        "parent_project_id": req.parent_project_id,
        "child_project_id": None,
    })

    return BridgeConnectResponse(
        ok=True,
        message="OAuth login thanh cong. Kenh da duoc map vao project.",
        parent_project_id=req.parent_project_id,
        channel_id=verified.channel_id,
        channel_name=verified.channel_title,
    )


@router.post("/disconnect", response_model=BridgeConnectResponse)
async def disconnect_project_channel(
    req: DisconnectRequest,
    session: SessionDep,
    x_account_connect_key: str = Header(default="", alias="X-Account-Connect-Key"),
) -> BridgeConnectResponse:
    _require_bridge_key(x_account_connect_key)
    project = await session.get(ParentProject, req.parent_project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Parent project not found.")

    disconnect_youtube_config(req.parent_project_id)
    save_youtube_inbox_cache(
        req.parent_project_id,
        items=[],
        ok=False,
        message="",
        synced_at="",
        source="",
    )

    await extension_manager.broadcast({
        "type": "extension_push",
        "feature": "account_connect",
        "content": json.dumps({
            "parent_project_id": req.parent_project_id,
            "channel_id": "",
            "channel_name": "",
            "provider_mode": "api",
            "connected": False,
        }),
        "parent_project_id": req.parent_project_id,
        "child_project_id": None,
    })

    return BridgeConnectResponse(
        ok=True,
        message="Da ngat ket noi kenh khoi project.",
        parent_project_id=req.parent_project_id,
        channel_id="",
        channel_name="",
    )


@router.post("/inbox-sync", response_model=BridgeInboxSyncResponse)
async def sync_inbox_snapshot(
    req: BridgeInboxSyncRequest,
    session: SessionDep,
    x_account_connect_key: str = Header(default="", alias="X-Account-Connect-Key"),
) -> BridgeInboxSyncResponse:
    _require_bridge_key(x_account_connect_key)

    project = await session.get(ParentProject, req.parent_project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Parent project not found.")

    payload = save_youtube_inbox_cache(
        req.parent_project_id,
        items=[item.model_dump() for item in req.items],
        ok=req.ok,
        message=req.message,
        synced_at=req.synced_at or datetime.now(timezone.utc).isoformat(),
        source=req.source,
    )
    return BridgeInboxSyncResponse(
        ok=True,
        message=payload["message"] or "Inbox cache synced.",
        parent_project_id=req.parent_project_id,
        item_count=len(payload["items"]),
    )
