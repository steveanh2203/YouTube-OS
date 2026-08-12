"""Storage + helpers for YTB Connect bridge and YouTube channel configs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
from typing import Any
from zoneinfo import ZoneInfo

from autocapcut.services.youtube_comments import DEFAULT_OPENROUTER_MODEL
from autocapcut.services.youtube_comments import DEFAULT_REPLY_SYSTEM_PROMPT


ACCOUNT_CONNECT_FILE_ENV = "AUTOCAPCUT_ACCOUNT_CONNECT_FILE"
DEFAULT_ACCOUNT_CONNECT_PATH = Path.home() / ".autocapcut" / "account_connect.json"
YOUTUBE_QUOTA_DAILY_LIMIT = 10_000
YOUTUBE_QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")


@dataclass(slots=True)
class BridgeSettings:
    bridge_key: str
    extension_name: str = "YTB Connect"


@dataclass(slots=True)
class OAuthAppSettings:
    client_id: str
    client_secret: str
    verified_at: str = ""


@dataclass(slots=True)
class AIReplySettings:
    openrouter_key: str
    primary_model: str = DEFAULT_OPENROUTER_MODEL
    fallback_models: list[str] | None = None
    verified_at: str = ""


def _state_path() -> Path:
    raw = os.environ.get(ACCOUNT_CONNECT_FILE_ENV, "").strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        path = DEFAULT_ACCOUNT_CONNECT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _empty_state() -> dict[str, Any]:
    return {
        "bridge_key": "",
        "oauth_settings": {},
        "youtube_data_settings": {},
        "ai33_settings": {},
        "ai_reply_settings": {},
        "youtube_configs": {},
        "youtube_inbox_cache": {},
        "youtube_quota_snapshot": {},
    }


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _empty_state()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()

    if not isinstance(payload, dict):
        return _empty_state()

    state = _empty_state()
    state["bridge_key"] = str(payload.get("bridge_key") or "").strip()
    raw_oauth_settings = payload.get("oauth_settings") or {}
    if isinstance(raw_oauth_settings, dict):
        state["oauth_settings"] = raw_oauth_settings
    raw_youtube_data_settings = payload.get("youtube_data_settings") or {}
    if isinstance(raw_youtube_data_settings, dict):
        state["youtube_data_settings"] = raw_youtube_data_settings
    raw_ai33_settings = payload.get("ai33_settings") or {}
    if isinstance(raw_ai33_settings, dict):
        state["ai33_settings"] = raw_ai33_settings
    raw_ai_reply_settings = payload.get("ai_reply_settings") or {}
    if isinstance(raw_ai_reply_settings, dict):
        state["ai_reply_settings"] = raw_ai_reply_settings
    raw_configs = payload.get("youtube_configs") or {}
    if isinstance(raw_configs, dict):
        state["youtube_configs"] = raw_configs
    raw_inbox_cache = payload.get("youtube_inbox_cache") or {}
    if isinstance(raw_inbox_cache, dict):
        state["youtube_inbox_cache"] = raw_inbox_cache
    raw_quota = payload.get("youtube_quota_snapshot") or {}
    if isinstance(raw_quota, dict):
        state["youtube_quota_snapshot"] = raw_quota
    return state


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    path.chmod(0o600)


def sanitize_youtube_config(value: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = value or {}
    return {
        "access_token": str(raw.get("access_token") or "").strip(),
        "refresh_token": str(raw.get("refresh_token") or "").strip(),
        "token_expiry": str(raw.get("token_expiry") or "").strip(),
        "channel_id": str(raw.get("channel_id") or "").strip(),
        "channel_name": str(raw.get("channel_name") or "").strip(),
        "openrouter_key": str(raw.get("openrouter_key") or "").strip(),
        "model": str(raw.get("model") or DEFAULT_OPENROUTER_MODEL).strip() or DEFAULT_OPENROUTER_MODEL,
        "system_prompt": str(raw.get("system_prompt") or DEFAULT_REPLY_SYSTEM_PROMPT).strip() or DEFAULT_REPLY_SYSTEM_PROMPT,
        "channel_dna": str(raw.get("channel_dna") or "").strip(),
        "audience_profile": str(raw.get("audience_profile") or "").strip(),
        "target_market": str(raw.get("target_market") or "").strip(),
        "auto_reply_mode": "safe-only" if str(raw.get("auto_reply_mode") or "").strip() == "safe-only" else "manual",
        "provider_mode": str(raw.get("provider_mode") or "api").strip() or "api",
        "connected": bool(raw.get("connected")),
        "verified_at": str(raw.get("verified_at") or "").strip(),
        "session_cookie": str(raw.get("session_cookie") or "").strip(),
        "session_index": str(raw.get("session_index") or "").strip(),
        "delegated_session_id": str(raw.get("delegated_session_id") or "").strip(),
        "visitor_data": str(raw.get("visitor_data") or "").strip(),
    }


def sanitize_oauth_settings(value: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = value or {}
    return {
        "client_id": str(raw.get("client_id") or "").strip(),
        "client_secret": str(raw.get("client_secret") or "").strip(),
        "verified_at": str(raw.get("verified_at") or "").strip(),
    }


def sanitize_ai33_settings(value: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = value or {}
    return {
        "api_key": str(raw.get("api_key") or "").strip(),
        "verified_at": str(raw.get("verified_at") or "").strip(),
    }


def sanitize_youtube_data_settings(value: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = value or {}
    return {
        "api_key": str(raw.get("api_key") or "").strip(),
        "verified_at": str(raw.get("verified_at") or "").strip(),
    }


def sanitize_ai_reply_settings(value: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = value or {}
    fallback_models_raw = raw.get("fallback_models")
    fallback_models: list[str] = []
    if isinstance(fallback_models_raw, list):
        seen: set[str] = set()
        for item in fallback_models_raw:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            fallback_models.append(text)

    primary_model = str(raw.get("primary_model") or DEFAULT_OPENROUTER_MODEL).strip() or DEFAULT_OPENROUTER_MODEL
    fallback_models = [item for item in fallback_models if item != primary_model][:4]

    return {
        "openrouter_key": str(raw.get("openrouter_key") or "").strip(),
        "primary_model": primary_model,
        "fallback_models": fallback_models,
        "verified_at": str(raw.get("verified_at") or "").strip(),
    }


def _current_pt_date() -> str:
    return datetime.now(YOUTUBE_QUOTA_TIMEZONE).date().isoformat()


def sanitize_youtube_quota_snapshot(value: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = value or {}
    today_pt = _current_pt_date()
    date_pt = str(raw.get("date_pt") or "").strip() or today_pt

    try:
        estimated_units_used = max(0, int(raw.get("estimated_units_used") or 0))
    except (TypeError, ValueError):
        estimated_units_used = 0

    breakdown_raw = raw.get("breakdown")
    breakdown: dict[str, int] = {}
    if isinstance(breakdown_raw, dict):
        for key, item in breakdown_raw.items():
            label = str(key or "").strip()
            if not label:
                continue
            try:
                units = max(0, int(item or 0))
            except (TypeError, ValueError):
                continue
            if units:
                breakdown[label] = units

    updated_at = str(raw.get("updated_at") or "").strip()

    if date_pt != today_pt:
        date_pt = today_pt
        estimated_units_used = 0
        breakdown = {}
        updated_at = ""

    return {
        "date_pt": date_pt,
        "estimated_units_used": estimated_units_used,
        "daily_limit": YOUTUBE_QUOTA_DAILY_LIMIT,
        "breakdown": breakdown,
        "updated_at": updated_at,
    }


def oauth_settings_ready(value: dict[str, Any] | None = None) -> bool:
    settings = sanitize_oauth_settings(value)
    client_id = settings["client_id"]
    client_secret = settings["client_secret"]
    return bool(
        client_id
        and client_secret
        and client_id.endswith(".apps.googleusercontent.com")
        and len(client_secret) >= 12
    )


def get_oauth_settings() -> dict[str, Any]:
    state = _load_state()
    raw = state.get("oauth_settings") if isinstance(state.get("oauth_settings"), dict) else {}
    return sanitize_oauth_settings(raw)


def get_ai33_settings() -> dict[str, Any]:
    state = _load_state()
    raw = state.get("ai33_settings") if isinstance(state.get("ai33_settings"), dict) else {}
    return sanitize_ai33_settings(raw)


def get_youtube_data_settings() -> dict[str, Any]:
    state = _load_state()
    raw = state.get("youtube_data_settings") if isinstance(state.get("youtube_data_settings"), dict) else {}
    return sanitize_youtube_data_settings(raw)


def get_ai_reply_settings() -> dict[str, Any]:
    state = _load_state()
    raw = state.get("ai_reply_settings") if isinstance(state.get("ai_reply_settings"), dict) else {}
    return sanitize_ai_reply_settings(raw)


def save_oauth_settings(value: dict[str, Any]) -> dict[str, Any]:
    state = _load_state()
    current = sanitize_oauth_settings(state.get("oauth_settings") if isinstance(state.get("oauth_settings"), dict) else None)
    merged = {**current, **(value or {})}
    sanitized = sanitize_oauth_settings(merged)
    state["oauth_settings"] = sanitized
    _save_state(state)
    return sanitized


def save_ai33_settings(value: dict[str, Any]) -> dict[str, Any]:
    state = _load_state()
    current = sanitize_ai33_settings(state.get("ai33_settings") if isinstance(state.get("ai33_settings"), dict) else None)
    merged = {**current, **(value or {})}
    sanitized = sanitize_ai33_settings(merged)
    state["ai33_settings"] = sanitized
    _save_state(state)
    return sanitized


def save_youtube_data_settings(value: dict[str, Any]) -> dict[str, Any]:
    state = _load_state()
    current = sanitize_youtube_data_settings(
        state.get("youtube_data_settings") if isinstance(state.get("youtube_data_settings"), dict) else None
    )
    merged = {**current, **(value or {})}
    sanitized = sanitize_youtube_data_settings(merged)
    state["youtube_data_settings"] = sanitized
    _save_state(state)
    return sanitized


def save_ai_reply_settings(value: dict[str, Any]) -> dict[str, Any]:
    state = _load_state()
    current = sanitize_ai_reply_settings(state.get("ai_reply_settings") if isinstance(state.get("ai_reply_settings"), dict) else None)
    merged = {**current, **(value or {})}
    sanitized = sanitize_ai_reply_settings(merged)
    state["ai_reply_settings"] = sanitized
    _save_state(state)
    return sanitized


def get_youtube_quota_snapshot() -> dict[str, Any]:
    state = _load_state()
    raw = state.get("youtube_quota_snapshot") if isinstance(state.get("youtube_quota_snapshot"), dict) else {}
    return sanitize_youtube_quota_snapshot(raw)


def save_youtube_quota_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    state = _load_state()
    current = sanitize_youtube_quota_snapshot(state.get("youtube_quota_snapshot") if isinstance(state.get("youtube_quota_snapshot"), dict) else None)
    merged = {**current, **(value or {})}
    sanitized = sanitize_youtube_quota_snapshot(merged)
    state["youtube_quota_snapshot"] = sanitized
    _save_state(state)
    return sanitized


def record_youtube_quota_usage(operation: str, units: int) -> dict[str, Any]:
    label = operation.strip()
    if not label or units <= 0:
        return get_youtube_quota_snapshot()

    snapshot = get_youtube_quota_snapshot()
    breakdown = dict(snapshot.get("breakdown") or {})
    breakdown[label] = max(0, int(breakdown.get(label) or 0)) + int(units)
    return save_youtube_quota_snapshot({
        "date_pt": snapshot["date_pt"],
        "estimated_units_used": int(snapshot.get("estimated_units_used") or 0) + int(units),
        "breakdown": breakdown,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def list_youtube_configs() -> dict[str, dict[str, Any]]:
    state = _load_state()
    configs = state.get("youtube_configs") or {}
    if not isinstance(configs, dict):
        return {}
    return {
        str(parent_id): sanitize_youtube_config(config if isinstance(config, dict) else None)
        for parent_id, config in configs.items()
    }


def get_youtube_config(parent_id: str | int) -> dict[str, Any]:
    key = str(parent_id).strip()
    return sanitize_youtube_config(list_youtube_configs().get(key))


def save_youtube_config(parent_id: str | int, value: dict[str, Any]) -> dict[str, Any]:
    key = str(parent_id).strip()
    if not key:
        raise ValueError("Parent project ID is required.")

    state = _load_state()
    configs = state.get("youtube_configs") or {}
    if not isinstance(configs, dict):
        configs = {}
    current = sanitize_youtube_config(configs.get(key) if isinstance(configs.get(key), dict) else None)
    merged = {**current, **(value or {})}
    sanitized = sanitize_youtube_config(merged)
    configs[key] = sanitized
    state["youtube_configs"] = configs
    _save_state(state)
    return sanitized


def save_verified_connection(
    *,
    parent_id: str | int,
    access_token: str,
    refresh_token: str = "",
    token_expiry: str = "",
    channel_id: str,
    channel_name: str,
    verified_at: str,
    provider_mode: str = "api",
    session_cookie: str = "",
    session_index: str = "",
    delegated_session_id: str = "",
    visitor_data: str = "",
) -> dict[str, Any]:
    current = get_youtube_config(parent_id)
    current.update({
        "access_token": access_token.strip(),
        "refresh_token": refresh_token.strip() or str(current.get("refresh_token") or "").strip(),
        "token_expiry": token_expiry.strip(),
        "channel_id": channel_id.strip(),
        "channel_name": channel_name.strip(),
        "provider_mode": provider_mode.strip() or "api",
        "connected": True,
        "verified_at": verified_at.strip(),
        "session_cookie": session_cookie.strip() if provider_mode.strip() == "session" else "",
        "session_index": session_index.strip() if provider_mode.strip() == "session" else "",
        "delegated_session_id": delegated_session_id.strip() if provider_mode.strip() == "session" else "",
        "visitor_data": visitor_data.strip() if provider_mode.strip() == "session" else "",
    })
    return save_youtube_config(parent_id, current)


def disconnect_youtube_config(parent_id: str | int) -> dict[str, Any]:
    current = get_youtube_config(parent_id)
    current.update({
        "access_token": "",
        "refresh_token": "",
        "token_expiry": "",
        "channel_id": "",
        "channel_name": "",
        "provider_mode": "api",
        "connected": False,
        "verified_at": "",
        "session_cookie": "",
        "session_index": "",
        "delegated_session_id": "",
        "visitor_data": "",
    })
    return save_youtube_config(parent_id, current)


def get_bridge_settings() -> BridgeSettings:
    state = _load_state()
    bridge_key = str(state.get("bridge_key") or "").strip()
    if not bridge_key:
        bridge_key = secrets.token_urlsafe(24)
        state["bridge_key"] = bridge_key
        _save_state(state)
    return BridgeSettings(bridge_key=bridge_key)


def regenerate_bridge_key() -> BridgeSettings:
    state = _load_state()
    state["bridge_key"] = secrets.token_urlsafe(24)
    _save_state(state)
    return BridgeSettings(bridge_key=state["bridge_key"])


def is_bridge_key_valid(value: str) -> bool:
    provided = value.strip()
    if not provided:
        return False
    current = get_bridge_settings()
    return secrets.compare_digest(provided, current.bridge_key)


def _sanitize_inbox_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    comment_id = str(value.get("comment_id") or "").strip()
    thread_id = str(value.get("thread_id") or comment_id).strip()
    text = str(value.get("text") or "").strip()
    if not comment_id or not text:
      return None
    return {
        "thread_id": thread_id,
        "comment_id": comment_id,
        "video_id": (str(value.get("video_id") or "").strip() or None),
        "video_title": (str(value.get("video_title") or "").strip() or None),
        "author_display_name": str(value.get("author_display_name") or "Unknown viewer").strip(),
        "author_channel_id": (str(value.get("author_channel_id") or "").strip() or None),
        "text": text,
        "published_at": (str(value.get("published_at") or "").strip() or None),
        "updated_at": (str(value.get("updated_at") or "").strip() or None),
        "reply_count": int(value.get("reply_count") or 0),
        "is_from_channel_owner": bool(value.get("is_from_channel_owner")),
        "can_auto_reply": bool(value.get("can_auto_reply")),
    }


def get_youtube_inbox_cache(parent_id: str | int) -> dict[str, Any]:
    key = str(parent_id).strip()
    state = _load_state()
    raw_cache = state.get("youtube_inbox_cache") or {}
    if not isinstance(raw_cache, dict):
        return {"ok": False, "items": [], "message": "", "synced_at": "", "source": ""}
    payload = raw_cache.get(key) if isinstance(raw_cache.get(key), dict) else {}
    items = []
    for item in payload.get("items") or []:
        sanitized = _sanitize_inbox_item(item)
        if sanitized:
            items.append(sanitized)
    return {
        "ok": bool(payload.get("ok")),
        "items": items,
        "message": str(payload.get("message") or "").strip(),
        "synced_at": str(payload.get("synced_at") or "").strip(),
        "source": str(payload.get("source") or "").strip(),
    }


def save_youtube_inbox_cache(
    parent_id: str | int,
    *,
    items: list[dict[str, Any]] | None = None,
    ok: bool,
    message: str = "",
    synced_at: str = "",
    source: str = "",
) -> dict[str, Any]:
    key = str(parent_id).strip()
    if not key:
        raise ValueError("Parent project ID is required.")

    state = _load_state()
    raw_cache = state.get("youtube_inbox_cache") or {}
    if not isinstance(raw_cache, dict):
        raw_cache = {}

    cleaned_items: list[dict[str, Any]] = []
    for item in items or []:
        sanitized = _sanitize_inbox_item(item)
        if sanitized:
            cleaned_items.append(sanitized)

    payload = {
        "ok": bool(ok),
        "items": cleaned_items,
        "message": str(message or "").strip(),
        "synced_at": str(synced_at or "").strip(),
        "source": str(source or "").strip(),
    }
    raw_cache[key] = payload
    state["youtube_inbox_cache"] = raw_cache
    _save_state(state)
    return payload
