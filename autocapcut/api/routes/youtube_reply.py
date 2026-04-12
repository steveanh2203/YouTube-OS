"""Routes for YouTube comment inbox, drafting, and replies."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from autocapcut.services.account_connect import (
    get_ai_reply_settings,
    get_oauth_settings,
    get_youtube_quota_snapshot,
    get_youtube_config,
    list_youtube_configs,
    oauth_settings_ready,
    record_youtube_quota_usage,
    save_ai_reply_settings,
    sanitize_youtube_config,
    save_youtube_config,
)
from autocapcut.services.youtube_comments import (
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_REPLY_SYSTEM_PROMPT,
    YouTubeCommentsError,
    generate_reply_draft,
    list_channel_comments,
    post_comment_reply,
    refresh_oauth_access_token,
    verify_openrouter_key,
    verify_channel_access,
)


router = APIRouter()


class YouTubeInboxRequest(BaseModel):
    parent_project_id: int | None = None
    access_token: str = ""
    channel_id: str = ""
    max_results: int = Field(default=20, ge=1, le=100)
    page_token: str = ""


class VerifyChannelRequest(BaseModel):
    access_token: str = Field(min_length=1)
    channel_id: str = Field(default="", max_length=120)


class VerifyChannelResponse(BaseModel):
    ok: bool
    connected: bool
    channel_id: str = ""
    channel_title: str = ""
    message: str


class YouTubeConfigModel(BaseModel):
    access_token: str = ""
    channel_id: str = ""
    channel_name: str = ""
    openrouter_key: str = ""
    model: str = DEFAULT_OPENROUTER_MODEL
    system_prompt: str = DEFAULT_REPLY_SYSTEM_PROMPT
    channel_dna: str = ""
    audience_profile: str = ""
    target_market: str = ""
    auto_reply_mode: str = "manual"
    provider_mode: str = "api"
    connected: bool = False
    verified_at: str = ""


class YouTubeConfigResponse(BaseModel):
    ok: bool
    config: YouTubeConfigModel
    message: str


class YouTubeConfigMapResponse(BaseModel):
    ok: bool
    items: dict[str, YouTubeConfigModel]


class YouTubeCommentOut(BaseModel):
    class ReplyItem(BaseModel):
        reply_id: str
        author_display_name: str
        author_channel_id: str | None = None
        text: str
        published_at: str | None = None
        updated_at: str | None = None
        is_from_channel_owner: bool

    thread_id: str
    comment_id: str
    video_id: str | None = None
    video_title: str | None = None
    author_display_name: str
    author_channel_id: str | None = None
    text: str
    published_at: str | None = None
    updated_at: str | None = None
    reply_count: int
    is_from_channel_owner: bool
    can_auto_reply: bool
    replies: list[ReplyItem] = Field(default_factory=list)


class YouTubeInboxResponse(BaseModel):
    ok: bool
    comments: list[YouTubeCommentOut]
    next_page_token: str | None = None
    message: str


class ReplyDraftRequest(BaseModel):
    api_key: str = Field(min_length=1)
    comment_text: str = Field(min_length=1, max_length=10000)
    channel_name: str = Field(default="", max_length=200)
    video_title: str = Field(default="", max_length=200)
    seeding_comments: str = Field(default="", max_length=5000)
    system_prompt: str = Field(default=DEFAULT_REPLY_SYSTEM_PROMPT, max_length=5000)
    model: str = Field(default=DEFAULT_OPENROUTER_MODEL, min_length=1, max_length=120)


class ReplyDraftResponse(BaseModel):
    ok: bool
    reply_text: str = ""
    should_skip: bool = False
    message: str


class VerifyOpenRouterRequest(BaseModel):
    api_key: str = Field(min_length=1)


class VerifyOpenRouterResponse(BaseModel):
    ok: bool
    message: str


class YouTubeAISettingsModel(BaseModel):
    openrouter_key: str = ""
    primary_model: str = DEFAULT_OPENROUTER_MODEL
    fallback_models: list[str] = Field(default_factory=list)
    verified_at: str = ""


class YouTubeAISettingsResponse(BaseModel):
    ok: bool
    settings: YouTubeAISettingsModel
    message: str


class YouTubeQuotaSnapshotModel(BaseModel):
    date_pt: str
    estimated_units_used: int
    daily_limit: int
    remaining_units: int
    usage_ratio: float
    updated_at: str = ""
    breakdown: dict[str, int] = Field(default_factory=dict)


class YouTubeQuotaResponse(BaseModel):
    ok: bool
    quota: YouTubeQuotaSnapshotModel
    message: str


class PostReplyRequest(BaseModel):
    parent_project_id: int | None = None
    access_token: str = ""
    parent_id: str = Field(min_length=1)
    video_id: str = ""
    reply_text: str = Field(min_length=1, max_length=1000)


class PostReplyResponse(BaseModel):
    ok: bool
    reply_id: str = ""
    message: str


def _token_is_stale(token_expiry: str) -> bool:
    value = token_expiry.strip()
    if not value:
        return True
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    return expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2)


async def _ensure_project_access_token(parent_project_id: int | str) -> dict[str, object]:
    config = get_youtube_config(parent_project_id)
    if not bool(config.get("connected")) or not str(config.get("channel_id") or "").strip():
        raise YouTubeCommentsError("Project nay chua pair kenh YouTube.")

    access_token = str(config.get("access_token") or "").strip()
    refresh_token = str(config.get("refresh_token") or "").strip()
    token_expiry = str(config.get("token_expiry") or "").strip()
    if access_token and not _token_is_stale(token_expiry):
        return config

    if not refresh_token:
        raise YouTubeCommentsError("Project nay chua co refresh token YouTube. Pair lai bang extension.")

    oauth_settings = get_oauth_settings()
    if not oauth_settings_ready(oauth_settings):
        raise YouTubeCommentsError("OAuth config chua san sang trong Settings.")

    refreshed = await refresh_oauth_access_token(
        client_id=str(oauth_settings.get("client_id") or ""),
        client_secret=str(oauth_settings.get("client_secret") or ""),
        refresh_token=refresh_token,
    )
    saved = save_youtube_config(parent_project_id, {
        "access_token": refreshed.access_token,
        "refresh_token": refreshed.refresh_token,
        "token_expiry": refreshed.expires_at,
        "provider_mode": "api",
        "connected": True,
    })
    return saved


def _public_config_model(value: dict[str, object]) -> YouTubeConfigModel:
    return YouTubeConfigModel(
        access_token="",
        channel_id=str(value.get("channel_id") or ""),
        channel_name=str(value.get("channel_name") or ""),
        openrouter_key=str(value.get("openrouter_key") or ""),
        model=str(value.get("model") or DEFAULT_OPENROUTER_MODEL),
        system_prompt=str(value.get("system_prompt") or DEFAULT_REPLY_SYSTEM_PROMPT),
        channel_dna=str(value.get("channel_dna") or ""),
        audience_profile=str(value.get("audience_profile") or ""),
        target_market=str(value.get("target_market") or ""),
        auto_reply_mode=str(value.get("auto_reply_mode") or "manual"),
        provider_mode="api",
        connected=bool(value.get("connected")),
        verified_at=str(value.get("verified_at") or ""),
    )


def _public_ai_settings_model(value: dict[str, object]) -> YouTubeAISettingsModel:
    return YouTubeAISettingsModel(
        openrouter_key=str(value.get("openrouter_key") or ""),
        primary_model=str(value.get("primary_model") or DEFAULT_OPENROUTER_MODEL),
        fallback_models=[
            str(item or "").strip()
            for item in (value.get("fallback_models") or [])
            if str(item or "").strip()
        ],
        verified_at=str(value.get("verified_at") or ""),
    )


def _public_quota_model(value: dict[str, object]) -> YouTubeQuotaSnapshotModel:
    daily_limit = max(0, int(value.get("daily_limit") or 0))
    estimated_units_used = max(0, int(value.get("estimated_units_used") or 0))
    remaining_units = max(0, daily_limit - estimated_units_used)
    usage_ratio = 0.0 if daily_limit <= 0 else min(1.0, estimated_units_used / daily_limit)
    return YouTubeQuotaSnapshotModel(
        date_pt=str(value.get("date_pt") or ""),
        estimated_units_used=estimated_units_used,
        daily_limit=daily_limit,
        remaining_units=remaining_units,
        usage_ratio=usage_ratio,
        updated_at=str(value.get("updated_at") or ""),
        breakdown={
            str(key): max(0, int(item or 0))
            for key, item in dict(value.get("breakdown") or {}).items()
            if str(key).strip()
        },
    )


@router.post("/verify", response_model=VerifyChannelResponse)
async def verify_channel(req: VerifyChannelRequest) -> VerifyChannelResponse:
    try:
        verified = await verify_channel_access(
            access_token=req.access_token,
            expected_channel_id=req.channel_id,
        )
        record_youtube_quota_usage("channel_verify", 1)
        return VerifyChannelResponse(
            ok=True,
            connected=True,
            channel_id=verified.channel_id,
            channel_title=verified.channel_title,
            message="YouTube channel verified.",
        )
    except YouTubeCommentsError as exc:
        return VerifyChannelResponse(
            ok=False,
            connected=False,
            channel_id="",
            channel_title="",
            message=str(exc),
        )


@router.get("/configs", response_model=YouTubeConfigMapResponse)
async def get_youtube_configs() -> YouTubeConfigMapResponse:
    raw = list_youtube_configs()
    return YouTubeConfigMapResponse(
        ok=True,
        items={key: _public_config_model(value) for key, value in raw.items()},
    )


@router.get("/config/{parent_id}", response_model=YouTubeConfigResponse)
async def get_parent_youtube_config(parent_id: str) -> YouTubeConfigResponse:
    config = get_youtube_config(parent_id)
    return YouTubeConfigResponse(
        ok=True,
        config=_public_config_model(config),
        message="Config loaded.",
    )


@router.put("/config/{parent_id}", response_model=YouTubeConfigResponse)
async def put_parent_youtube_config(parent_id: str, req: YouTubeConfigModel) -> YouTubeConfigResponse:
    current = get_youtube_config(parent_id)
    editable = sanitize_youtube_config({
        **current,
        "channel_dna": req.channel_dna,
        "audience_profile": req.audience_profile,
        "target_market": req.target_market,
        "auto_reply_mode": req.auto_reply_mode,
    })
    saved = save_youtube_config(parent_id, editable)
    return YouTubeConfigResponse(
        ok=True,
        config=_public_config_model(saved),
        message="Config saved.",
    )


@router.get("/ai-settings", response_model=YouTubeAISettingsResponse)
async def get_ai_settings() -> YouTubeAISettingsResponse:
    settings = get_ai_reply_settings()
    return YouTubeAISettingsResponse(
        ok=True,
        settings=_public_ai_settings_model(settings),
        message="AI reply settings loaded.",
    )


@router.put("/ai-settings", response_model=YouTubeAISettingsResponse)
async def put_ai_settings(req: YouTubeAISettingsModel) -> YouTubeAISettingsResponse:
    saved = save_ai_reply_settings({
        "openrouter_key": req.openrouter_key,
        "primary_model": req.primary_model,
        "fallback_models": req.fallback_models,
        "verified_at": req.verified_at,
    })
    return YouTubeAISettingsResponse(
        ok=True,
        settings=_public_ai_settings_model(saved),
        message="AI reply settings saved.",
    )


@router.get("/quota", response_model=YouTubeQuotaResponse)
async def get_quota_snapshot() -> YouTubeQuotaResponse:
    quota = get_youtube_quota_snapshot()
    return YouTubeQuotaResponse(
        ok=True,
        quota=_public_quota_model(quota),
        message="YouTube quota snapshot loaded.",
    )


@router.post("/inbox", response_model=YouTubeInboxResponse)
async def get_comment_inbox(req: YouTubeInboxRequest) -> YouTubeInboxResponse:
    try:
        if req.parent_project_id is not None:
            config = await _ensure_project_access_token(req.parent_project_id)
            channel_id = str(req.channel_id or config.get("channel_id") or "").strip()
            page = await list_channel_comments(
                access_token=str(config.get("access_token") or "").strip(),
                channel_id=channel_id,
                max_results=req.max_results,
                page_token=req.page_token,
            )
        else:
            page = await list_channel_comments(
                access_token=req.access_token,
                channel_id=req.channel_id,
                max_results=req.max_results,
                page_token=req.page_token,
            )
        record_youtube_quota_usage("load_inbox", 1)
        return YouTubeInboxResponse(
            ok=True,
            comments=[YouTubeCommentOut(**asdict(item)) for item in page.items],
            next_page_token=page.next_page_token,
            message=f"Loaded {len(page.items)} comment(s).",
        )
    except YouTubeCommentsError as exc:
        return YouTubeInboxResponse(ok=False, comments=[], next_page_token=None, message=str(exc))


@router.post("/draft", response_model=ReplyDraftResponse)
async def draft_comment_reply(req: ReplyDraftRequest) -> ReplyDraftResponse:
    try:
        result = await generate_reply_draft(
            api_key=req.api_key,
            comment_text=req.comment_text,
            channel_name=req.channel_name,
            video_title=req.video_title,
            seeding_comments=req.seeding_comments,
            system_prompt=req.system_prompt,
            model=req.model,
        )
        return ReplyDraftResponse(
            ok=True,
            reply_text=result.reply_text,
            should_skip=result.should_skip,
            message="Draft generated." if not result.should_skip else "Draft skipped by model.",
        )
    except YouTubeCommentsError as exc:
        return ReplyDraftResponse(ok=False, reply_text="", should_skip=False, message=str(exc))


@router.post("/verify-openrouter", response_model=VerifyOpenRouterResponse)
async def verify_openrouter(req: VerifyOpenRouterRequest) -> VerifyOpenRouterResponse:
    try:
        await verify_openrouter_key(api_key=req.api_key)
        return VerifyOpenRouterResponse(ok=True, message="OpenRouter key verified.")
    except YouTubeCommentsError as exc:
        return VerifyOpenRouterResponse(ok=False, message=str(exc))


@router.post("/reply", response_model=PostReplyResponse)
async def reply_to_comment(req: PostReplyRequest) -> PostReplyResponse:
    try:
        if req.parent_project_id is not None:
            config = await _ensure_project_access_token(req.parent_project_id)
            reply_id = await post_comment_reply(
                access_token=str(config.get("access_token") or "").strip(),
                parent_id=req.parent_id,
                reply_text=req.reply_text,
            )
        else:
            reply_id = await post_comment_reply(
                access_token=req.access_token,
                parent_id=req.parent_id,
                reply_text=req.reply_text,
            )
        record_youtube_quota_usage("post_reply", 50)
        return PostReplyResponse(
            ok=True,
            reply_id=reply_id,
            message="Reply posted successfully.",
        )
    except YouTubeCommentsError as exc:
        return PostReplyResponse(ok=False, reply_id="", message=str(exc))
