"""Helpers for reading and replying to YouTube comments."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any

import httpx
from loguru import logger


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "openrouter/free"
DEFAULT_REPLY_SYSTEM_PROMPT = (
    "You reply to YouTube comments for the channel owner.\n"
    "Be warm, polite, natural, and concise.\n"
    "Thank the viewer when it fits naturally.\n"
    "When relevant, invite them to subscribe for more videos, but never sound pushy.\n"
    "Adapt to the comment. Answer questions clearly, acknowledge praise, and handle criticism calmly.\n"
    "Keep replies short and clean. No fluff, no hashtags, no emojis, no fake hype.\n"
    "If the comment is spam, abusive, unrelated, or should not be answered, respond with exactly SKIP."
)
ROOT_DIR = Path(__file__).resolve().parents[2]
YOUTUBE_SESSION_BRIDGE = ROOT_DIR / "frontend" / "scripts" / "youtube_session_bridge.mjs"


class YouTubeCommentsError(RuntimeError):
    """Raised when YouTube/OpenRouter comment actions fail."""


@dataclass(slots=True)
class CommentReplyItem:
    reply_id: str
    author_display_name: str
    author_channel_id: str | None
    text: str
    published_at: str | None
    updated_at: str | None
    is_from_channel_owner: bool


@dataclass(slots=True)
class CommentInboxItem:
    thread_id: str
    comment_id: str
    video_id: str | None
    video_title: str | None
    author_display_name: str
    author_channel_id: str | None
    text: str
    published_at: str | None
    updated_at: str | None
    reply_count: int
    is_from_channel_owner: bool
    can_auto_reply: bool
    replies: list[CommentReplyItem]


@dataclass(slots=True)
class CommentInboxPage:
    items: list[CommentInboxItem]
    next_page_token: str | None


@dataclass(slots=True)
class DraftReplyResult:
    reply_text: str
    should_skip: bool


@dataclass(slots=True)
class VerifiedChannel:
    channel_id: str
    channel_title: str


@dataclass(slots=True)
class OAuthTokenBundle:
    access_token: str
    refresh_token: str
    expires_at: str


def _youtube_headers(access_token: str) -> dict[str, str]:
    token = access_token.strip()
    if not token:
        raise YouTubeCommentsError("YouTube access token is required.")
    return {"Authorization": f"Bearer {token}"}


def _node_bin() -> str:
    return shutil.which("node") or "node"


def expires_at_from_seconds(expires_in: int | float | str | None) -> str:
    try:
        ttl_seconds = max(0, int(float(expires_in or 0)))
    except (TypeError, ValueError):
        ttl_seconds = 0
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    return expires_at.isoformat()


async def _run_session_bridge(payload: dict[str, Any]) -> dict[str, Any]:
    if not YOUTUBE_SESSION_BRIDGE.exists():
        raise YouTubeCommentsError("Session bridge script is missing.")

    try:
        proc = await asyncio.create_subprocess_exec(
            _node_bin(),
            str(YOUTUBE_SESSION_BRIDGE),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise YouTubeCommentsError(f"Cannot start Node helper: {exc}") from exc

    stdout, stderr = await proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    raw_stdout = stdout.decode("utf-8", errors="replace").strip()
    raw_stderr = stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        try:
            parsed = json.loads(raw_stdout or "{}")
        except Exception:
            parsed = {}
        message = str(parsed.get("error") or "").strip() or raw_stderr or "Session helper failed."
        raise YouTubeCommentsError(message)

    try:
        data = json.loads(raw_stdout or "{}")
    except json.JSONDecodeError as exc:
        raise YouTubeCommentsError("Session helper returned invalid JSON.") from exc

    if not isinstance(data, dict) or not data.get("ok"):
        raise YouTubeCommentsError(str(data.get("error") or "Session helper failed."))
    return data


def _openrouter_headers(api_key: str) -> dict[str, str]:
    key = api_key.strip()
    if not key:
        raise YouTubeCommentsError("OpenRouter API key is required.")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://127.0.0.1:1420",
        "X-Title": "MasterOS YouTube Reply",
    }


def _map_reply_item(raw: dict[str, Any], owner_channel_id: str) -> CommentReplyItem | None:
    snippet = raw.get("snippet") or {}
    reply_id = str(raw.get("id") or "").strip()
    text = str(snippet.get("textDisplay") or snippet.get("textOriginal") or "").strip()
    if not reply_id or not text:
        return None

    author_channel = snippet.get("authorChannelId") or {}
    author_channel_id = author_channel.get("value") if isinstance(author_channel, dict) else None
    is_from_owner = bool(owner_channel_id.strip()) and author_channel_id == owner_channel_id.strip()

    return CommentReplyItem(
        reply_id=reply_id,
        author_display_name=str(snippet.get("authorDisplayName") or "Channel").strip(),
        author_channel_id=author_channel_id,
        text=text,
        published_at=(str(snippet.get("publishedAt")).strip() or None) if snippet.get("publishedAt") else None,
        updated_at=(str(snippet.get("updatedAt")).strip() or None) if snippet.get("updatedAt") else None,
        is_from_channel_owner=is_from_owner,
    )


def _extract_text_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _normalize_reply_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```$", "", cleaned)
    cleaned = cleaned.strip().strip('"').strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _build_brand_voice(seeding_comments: str) -> str:
    seed_lines = [line.strip() for line in seeding_comments.splitlines() if line.strip()]
    if not seed_lines:
        return ""
    limited = seed_lines[:5]
    return "\n".join(f"- {line}" for line in limited)


def _build_openrouter_messages(
    *,
    comment_text: str,
    channel_name: str,
    video_title: str,
    seeding_comments: str,
    system_prompt: str = "",
) -> list[dict[str, str]]:
    brand_voice = _build_brand_voice(seeding_comments)
    system_parts = [
        (system_prompt or DEFAULT_REPLY_SYSTEM_PROMPT).strip(),
        "Keep replies under 280 characters unless the viewer asks a direct question that needs more detail.",
    ]
    if brand_voice:
        system_parts.append("Match this channel voice when helpful:\n" + brand_voice)

    context_bits = []
    if channel_name.strip():
        context_bits.append(f"Channel: {channel_name.strip()}")
    if video_title.strip():
        context_bits.append(f"Video title: {video_title.strip()}")

    user_prompt = []
    if context_bits:
        user_prompt.append("\n".join(context_bits))
    user_prompt.append(f"Viewer comment:\n{comment_text.strip()}")
    user_prompt.append("Return only the reply text. No explanation.")

    return [
        {"role": "system", "content": "\n\n".join(part for part in system_parts if part.strip())},
        {"role": "user", "content": "\n\n".join(user_prompt)},
    ]


async def verify_openrouter_key(*, api_key: str) -> None:
    payload = {
        "model": DEFAULT_OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 8,
        "temperature": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                OPENROUTER_CHAT_URL,
                headers=_openrouter_headers(api_key),
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise YouTubeCommentsError(f"OpenRouter verify request failed: {exc}") from exc

    if response.status_code == 401:
        raise YouTubeCommentsError("OpenRouter auth failed. API key may be invalid.")
    if response.status_code >= 400:
        raise YouTubeCommentsError(f"OpenRouter returned {response.status_code}: {response.text}")


def _map_comment_item(raw: dict[str, Any], owner_channel_id: str) -> CommentInboxItem:
    snippet = raw.get("snippet") or {}
    top_level = snippet.get("topLevelComment") or {}
    top_snippet = top_level.get("snippet") or {}
    author_channel = top_snippet.get("authorChannelId") or {}
    author_channel_id = author_channel.get("value") if isinstance(author_channel, dict) else None
    text = str(top_snippet.get("textDisplay") or top_snippet.get("textOriginal") or "").strip()
    reply_count = int(snippet.get("totalReplyCount") or 0)
    comment_id = str(top_level.get("id") or "").strip()
    is_from_owner = bool(owner_channel_id.strip()) and author_channel_id == owner_channel_id.strip()
    replies_raw = (raw.get("replies") or {}).get("comments") or []
    replies = [
        mapped
        for item in replies_raw
        if isinstance(item, dict)
        for mapped in [_map_reply_item(item, owner_channel_id)]
        if mapped is not None
    ]

    return CommentInboxItem(
        thread_id=str(raw.get("id") or "").strip(),
        comment_id=comment_id,
        video_id=(str(snippet.get("videoId")).strip() or None) if snippet.get("videoId") else None,
        video_title=None,
        author_display_name=str(top_snippet.get("authorDisplayName") or "Unknown viewer").strip(),
        author_channel_id=author_channel_id,
        text=text,
        published_at=(str(top_snippet.get("publishedAt")).strip() or None) if top_snippet.get("publishedAt") else None,
        updated_at=(str(top_snippet.get("updatedAt")).strip() or None) if top_snippet.get("updatedAt") else None,
        reply_count=reply_count,
        is_from_channel_owner=is_from_owner,
        can_auto_reply=bool(comment_id and text and reply_count == 0 and not is_from_owner),
        replies=replies,
    )


async def verify_channel_access(
    *,
    access_token: str,
    expected_channel_id: str = "",
) -> VerifiedChannel:
    params: dict[str, Any] = {
        "part": "snippet",
        "mine": "true",
        "maxResults": 50,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{YOUTUBE_API_BASE}/channels",
                params=params,
                headers=_youtube_headers(access_token),
            )
    except httpx.HTTPError as exc:
        raise YouTubeCommentsError(f"YouTube verify request failed: {exc}") from exc

    if response.status_code == 401:
        raise YouTubeCommentsError("YouTube auth failed. Access token may be expired.")
    if response.status_code >= 400:
        raise YouTubeCommentsError(f"YouTube returned {response.status_code}: {response.text}")

    payload = response.json()
    channels = payload.get("items") or []
    if not channels:
        raise YouTubeCommentsError("No YouTube channel found for this access token.")

    expected = expected_channel_id.strip()
    picked: dict[str, Any] | None = None
    if expected:
        for item in channels:
            if str(item.get("id") or "").strip() == expected:
                picked = item
                break
        if picked is None:
            raise YouTubeCommentsError("This token does not match the selected channel ID.")
    else:
        picked = channels[0]

    snippet = picked.get("snippet") or {}
    channel_id = str(picked.get("id") or "").strip()
    channel_title = str(snippet.get("title") or "").strip() or "Untitled channel"
    if not channel_id:
        raise YouTubeCommentsError("YouTube verify response did not include a channel ID.")

    return VerifiedChannel(channel_id=channel_id, channel_title=channel_title)


async def exchange_oauth_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> OAuthTokenBundle:
    payload = {
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "code": code.strip(),
        "redirect_uri": redirect_uri.strip(),
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload)
    except httpx.HTTPError as exc:
        raise YouTubeCommentsError(f"Google OAuth exchange failed: {exc}") from exc

    if response.status_code >= 400:
        raise YouTubeCommentsError(f"Google OAuth exchange failed: {response.text}")

    data = response.json()
    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise YouTubeCommentsError("Google OAuth did not return access token or refresh token.")

    return OAuthTokenBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at_from_seconds(data.get("expires_in")),
    )


async def refresh_oauth_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> OAuthTokenBundle:
    payload = {
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "refresh_token": refresh_token.strip(),
        "grant_type": "refresh_token",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload)
    except httpx.HTTPError as exc:
        raise YouTubeCommentsError(f"Google OAuth refresh failed: {exc}") from exc

    if response.status_code >= 400:
        raise YouTubeCommentsError(f"Google OAuth refresh failed: {response.text}")

    data = response.json()
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise YouTubeCommentsError("Google OAuth refresh did not return an access token.")

    returned_refresh_token = str(data.get("refresh_token") or "").strip() or refresh_token.strip()
    return OAuthTokenBundle(
        access_token=access_token,
        refresh_token=returned_refresh_token,
        expires_at=expires_at_from_seconds(data.get("expires_in")),
    )


async def list_channel_comments(
    *,
    access_token: str,
    channel_id: str,
    max_results: int = 20,
    page_token: str | None = None,
) -> CommentInboxPage:
    if not channel_id.strip():
        raise YouTubeCommentsError("Channel ID is required.")

    params: dict[str, Any] = {
        "allThreadsRelatedToChannelId": channel_id.strip(),
        "part": "snippet,replies",
        "maxResults": max(1, min(max_results, 100)),
        "order": "time",
        "textFormat": "plainText",
    }
    if page_token and page_token.strip():
        params["pageToken"] = page_token.strip()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{YOUTUBE_API_BASE}/commentThreads",
                params=params,
                headers=_youtube_headers(access_token),
            )
    except httpx.HTTPError as exc:
        raise YouTubeCommentsError(f"YouTube request failed: {exc}") from exc

    if response.status_code == 401:
        raise YouTubeCommentsError("YouTube auth failed. Access token may be expired.")
    if response.status_code >= 400:
        raise YouTubeCommentsError(f"YouTube returned {response.status_code}: {response.text}")

    payload = response.json()
    items = [_map_comment_item(item, channel_id) for item in payload.get("items") or []]
    return CommentInboxPage(items=items, next_page_token=payload.get("nextPageToken"))


async def list_channel_comments_session(
    *,
    session_cookie: str,
    channel_id: str,
    max_results: int = 20,
    page_token: str | None = None,
    session_index: str = "",
    delegated_session_id: str = "",
    visitor_data: str = "",
) -> CommentInboxPage:
    if not channel_id.strip():
        raise YouTubeCommentsError("Channel ID is required.")
    if page_token and page_token.strip():
        logger.debug("Session inbox ignores page token for now: {}", page_token)

    data = await _run_session_bridge({
        "action": "list_inbox",
        "channel_id": channel_id.strip(),
        "max_results": max_results,
        "session": {
            "session_cookie": session_cookie.strip(),
            "session_index": session_index.strip(),
            "delegated_session_id": delegated_session_id.strip(),
            "visitor_data": visitor_data.strip(),
        },
    })

    items: list[CommentInboxItem] = []
    for raw in data.get("comments") or []:
        if not isinstance(raw, dict):
            continue
        items.append(CommentInboxItem(
            thread_id=str(raw.get("thread_id") or raw.get("comment_id") or "").strip(),
            comment_id=str(raw.get("comment_id") or "").strip(),
            video_id=(str(raw.get("video_id")).strip() or None) if raw.get("video_id") else None,
            video_title=(str(raw.get("video_title")).strip() or None) if raw.get("video_title") else None,
            author_display_name=str(raw.get("author_display_name") or "Unknown viewer").strip(),
            author_channel_id=(str(raw.get("author_channel_id")).strip() or None) if raw.get("author_channel_id") else None,
            text=str(raw.get("text") or "").strip(),
            published_at=(str(raw.get("published_at")).strip() or None) if raw.get("published_at") else None,
            updated_at=(str(raw.get("updated_at")).strip() or None) if raw.get("updated_at") else None,
            reply_count=int(raw.get("reply_count") or 0),
            is_from_channel_owner=bool(raw.get("is_from_channel_owner")),
            can_auto_reply=bool(raw.get("can_auto_reply")),
            replies=[],
        ))

    return CommentInboxPage(items=items, next_page_token=data.get("next_page_token"))


async def generate_reply_draft(
    *,
    api_key: str,
    comment_text: str,
    channel_name: str = "",
    video_title: str = "",
    seeding_comments: str = "",
    system_prompt: str = "",
    model: str = DEFAULT_OPENROUTER_MODEL,
) -> DraftReplyResult:
    text = comment_text.strip()
    if not text:
        raise YouTubeCommentsError("Comment text is required.")

    payload = {
        "model": model.strip() or DEFAULT_OPENROUTER_MODEL,
        "messages": _build_openrouter_messages(
            comment_text=text,
            channel_name=channel_name,
            video_title=video_title,
            seeding_comments=seeding_comments,
            system_prompt=system_prompt,
        ),
        "temperature": 0.5,
    }

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                OPENROUTER_CHAT_URL,
                headers=_openrouter_headers(api_key),
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise YouTubeCommentsError(f"OpenRouter request failed: {exc}") from exc

    if response.status_code == 401:
        raise YouTubeCommentsError("OpenRouter auth failed. API key may be invalid.")
    if response.status_code >= 400:
        raise YouTubeCommentsError(f"OpenRouter returned {response.status_code}: {response.text}")

    data = response.json()
    choices = data.get("choices") or []
    first_choice = choices[0] if choices else {}
    message = first_choice.get("message") or {}
    content = _extract_text_content(message.get("content"))
    normalized = _normalize_reply_text(content)

    if not normalized:
        raise YouTubeCommentsError("OpenRouter returned an empty draft.")

    if normalized.upper() == "SKIP":
        return DraftReplyResult(reply_text="", should_skip=True)

    return DraftReplyResult(reply_text=normalized, should_skip=False)


async def post_comment_reply(
    *,
    access_token: str,
    parent_id: str,
    reply_text: str,
) -> str:
    parent = parent_id.strip()
    text = reply_text.strip()
    if not parent:
        raise YouTubeCommentsError("Parent comment ID is required.")
    if not text:
        raise YouTubeCommentsError("Reply text is required.")

    body = {"snippet": {"parentId": parent, "textOriginal": text}}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{YOUTUBE_API_BASE}/comments",
                params={"part": "snippet"},
                headers=_youtube_headers(access_token),
                json=body,
            )
    except httpx.HTTPError as exc:
        raise YouTubeCommentsError(f"YouTube reply request failed: {exc}") from exc

    if response.status_code == 401:
        raise YouTubeCommentsError("YouTube auth failed while posting reply.")
    if response.status_code >= 400:
        raise YouTubeCommentsError(f"YouTube returned {response.status_code}: {response.text}")

    payload = response.json()
    reply_id = str(payload.get("id") or "").strip()
    if not reply_id:
        logger.warning("YouTube reply posted but response had no id: {}", json.dumps(payload))
    return reply_id


async def post_comment_reply_session(
    *,
    session_cookie: str,
    video_id: str,
    parent_id: str,
    reply_text: str,
    session_index: str = "",
    delegated_session_id: str = "",
    visitor_data: str = "",
) -> str:
    data = await _run_session_bridge({
        "action": "reply_comment",
        "video_id": video_id.strip(),
        "comment_id": parent_id.strip(),
        "reply_text": reply_text.strip(),
        "session": {
            "session_cookie": session_cookie.strip(),
            "session_index": session_index.strip(),
            "delegated_session_id": delegated_session_id.strip(),
            "visitor_data": visitor_data.strip(),
        },
    })
    return str(data.get("reply_id") or parent_id).strip()
