"""Business rules and publish helpers for Community posts."""
from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from autocapcut.database.models import CommunityPost
from autocapcut.services.media_workspace import resolve_managed_path, resolve_workspace_file_reference
from autocapcut.services.community_dom_publisher import (
    CommunityDomPublishError,
    publish_community_post_via_roxy,
)


VALID_COMMUNITY_STATUSES = {"draft", "queued", "posting", "published", "failed", "cancelled"}
VALID_POST_MODES = {"now", "schedule"}
MAX_LAST_ERROR_LEN = 2000
UNSET = object()
DB_PATH = Path.home() / ".autocapcut" / "autocapcut.db"
_LOG_LOCK = threading.Lock()
_LOGS: dict[int, list[dict[str, Any]]] = defaultdict(list)


class CommunityPostingError(RuntimeError):
    """Raised when Community post lifecycle rules are violated."""


@dataclass(slots=True)
class PublishRuntimeInput:
    """Runtime-only input supplied from the frontend, never persisted."""

    api_host: str
    api_token: str


def utcnow() -> datetime:
    return datetime.utcnow()


def normalize_post_body(raw: str) -> str:
    lines = [line.rstrip() for line in str(raw or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def normalize_post_mode(raw: str | None) -> str:
    value = str(raw or "now").strip().lower()
    if value not in VALID_POST_MODES:
        raise CommunityPostingError(f"Invalid post mode: {raw}")
    return value


def normalize_channel_url(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        raise CommunityPostingError("Channel URL is invalid.")
    return value.rstrip("/")


def clip_error_message(raw: str) -> str:
    value = str(raw or "").strip()
    if len(value) <= MAX_LAST_ERROR_LEN:
        return value
    return value[: MAX_LAST_ERROR_LEN - 1].rstrip() + "…"


def append_post_log(post_id: int, level: str, message: str, meta: dict[str, Any] | None = None) -> None:
    entry = {
        "ts": utcnow().isoformat(timespec="seconds"),
        "level": level.strip().lower() or "info",
        "message": str(message or "").strip(),
        "meta": meta or None,
    }
    with _LOG_LOCK:
        _LOGS[post_id].append(entry)
        _LOGS[post_id] = _LOGS[post_id][-80:]


def get_post_logs(post_id: int) -> list[dict[str, Any]]:
    with _LOG_LOCK:
        return list(_LOGS.get(post_id, []))


def apply_post_payload(
    post: CommunityPost,
    *,
    body: str | None | object = UNSET,
    image_path: str | None | object = UNSET,
    child_project_id: int | None | object = UNSET,
    channel_url: str | None | object = UNSET,
    post_mode: str | None | object = UNSET,
    schedule_at: datetime | None | object = UNSET,
) -> CommunityPost:
    if body is not UNSET:
        post.body = normalize_post_body(body)
    if image_path is not UNSET:
        post.image_path = str(image_path).strip() or None
    if child_project_id is not UNSET:
        post.child_project_id = child_project_id if isinstance(child_project_id, int) else None
    if channel_url is not UNSET:
        post.channel_url = normalize_channel_url(channel_url)
    if post_mode is not UNSET:
        post.post_mode = normalize_post_mode(post_mode)
    if schedule_at is not UNSET:
        post.schedule_at = schedule_at if isinstance(schedule_at, datetime) else None

    post.updated_at = utcnow()
    return post


def resolve_community_image_path(reference: str) -> Path:
    """Resolve a persisted opaque image reference only when the publisher needs it."""
    value = reference.strip()
    if value.startswith("workspace-file:"):
        return resolve_workspace_file_reference(value)
    if value.startswith("media:"):
        asset_id = value.removeprefix("media:").strip()
        if not asset_id:
            raise CommunityPostingError("Invalid media image reference.")
        try:
            with closing(_connect_sqlite()) as connection:
                row = connection.execute(
                    "SELECT stored_path FROM media_assets WHERE id = ?",
                    (asset_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise CommunityPostingError("Could not resolve the media image reference.") from exc
        if row is None:
            raise CommunityPostingError("Community image is missing from Media Library.")
        return resolve_managed_path(str(row["stored_path"]))
    return Path(value).expanduser()


def ensure_post_publishable(post: CommunityPost) -> None:
    if not normalize_post_body(post.body):
        raise CommunityPostingError("Post body is required before queueing.")
    if not post.channel_url:
        raise CommunityPostingError("Channel URL is required before queueing.")
    if post.image_path:
        image_path = resolve_community_image_path(post.image_path)
        if not image_path.exists() or not image_path.is_file():
            raise CommunityPostingError(f"Image file does not exist: {post.image_path}")
    post.post_mode = normalize_post_mode(post.post_mode)
    if post.post_mode == "schedule" and post.schedule_at is None:
        raise CommunityPostingError("Schedule time is required when post mode is schedule.")


def queue_post(post: CommunityPost) -> CommunityPost:
    if post.status == "published":
        raise CommunityPostingError("Published posts cannot be queued again.")
    if post.status == "posting":
        raise CommunityPostingError("This post is already being published right now.")
    ensure_post_publishable(post)
    now = utcnow()
    post.status = "queued"
    post.queued_at = now
    post.posting_started_at = None
    post.last_error = None
    post.updated_at = now
    return post


def retry_post(post: CommunityPost, *, force: bool = False) -> CommunityPost:
    if post.status != "failed":
        raise CommunityPostingError("Only failed posts can be retried.")
    if not force and (post.youtube_post_url or post.youtube_post_id):
        raise CommunityPostingError(
            "This post already has a YouTube result attached. Use force retry only after manual verification."
        )
    return queue_post(post)


def should_publish_now(post: CommunityPost, now: datetime | None = None) -> bool:
    moment = now or utcnow()
    if post.status != "queued":
        return False
    if post.post_mode == "now":
        return True
    if post.schedule_at is None:
        return False
    return post.schedule_at <= moment


def _connect_sqlite() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def publish_queued_post(post_id: int, runtime: PublishRuntimeInput) -> dict[str, Any]:
    append_post_log(post_id, "info", "Publish requested", {"mode": "dom"})

    with closing(_connect_sqlite()) as conn:
        row = conn.execute(
            """
            SELECT cp.*, p.roxy_workspace_id, p.roxy_profile_id, p.roxy_profile_name
            FROM community_posts cp
            JOIN parent_projects p ON p.id = cp.parent_project_id
            WHERE cp.id = ?
            """,
            (post_id,),
        ).fetchone()

        if row is None:
            raise CommunityPostingError("Community post not found.")
        if row["status"] != "queued":
            raise CommunityPostingError("Only queued posts can be published.")

        now = utcnow().isoformat(sep=" ", timespec="seconds")
        conn.execute(
            """
            UPDATE community_posts
            SET status = 'posting',
                posting_started_at = ?,
                last_attempt_at = ?,
                attempt_count = attempt_count + 1,
                updated_at = ?,
                last_error = NULL
            WHERE id = ?
            """,
            (now, now, now, post_id),
        )
        conn.commit()

        row = conn.execute(
            """
            SELECT cp.*, p.roxy_workspace_id, p.roxy_profile_id, p.roxy_profile_name
            FROM community_posts cp
            JOIN parent_projects p ON p.id = cp.parent_project_id
            WHERE cp.id = ?
            """,
            (post_id,),
        ).fetchone()

        workspace_id = int(row["roxy_workspace_id"] or 0)
        profile_id = str(row["roxy_profile_id"] or "").strip()
        if workspace_id <= 0 or not profile_id:
            message = "Parent project is missing a Roxy workspace/profile mapping."
            conn.execute(
                """
                UPDATE community_posts
                SET status = 'failed', last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (message, now, post_id),
            )
            conn.commit()
            append_post_log(post_id, "error", message)
            raise CommunityPostingError(message)

    try:
        summary = publish_community_post_via_roxy(
            api_host=runtime.api_host,
            api_token=runtime.api_token,
            workspace_id=workspace_id,
            profile_id=profile_id,
            channel_url=str(row["channel_url"] or ""),
            body=str(row["body"] or ""),
            image_path=(
                str(resolve_community_image_path(str(row["image_path"])))
                if str(row["image_path"] or "").strip()
                else None
            ),
        )
    except (CommunityDomPublishError, CommunityPostingError, RuntimeError) as exc:
        error_message = clip_error_message(str(exc))
        with closing(_connect_sqlite()) as conn:
            conn.execute(
                """
                UPDATE community_posts
                SET status = 'failed',
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_message, utcnow().isoformat(sep=" ", timespec="seconds"), post_id),
            )
            conn.commit()
        append_post_log(post_id, "error", error_message)
        logger.warning("Community publish failed for post_id={}: {}", post_id, error_message)
        raise

    published_at = utcnow().isoformat(sep=" ", timespec="seconds")
    with closing(_connect_sqlite()) as conn:
        conn.execute(
            """
            UPDATE community_posts
            SET status = 'published',
                published_at = ?,
                youtube_post_url = ?,
                youtube_post_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                published_at,
                summary.youtube_post_url,
                summary.youtube_post_id,
                published_at,
                post_id,
            ),
        )
        conn.commit()
        final_row = conn.execute("SELECT * FROM community_posts WHERE id = ?", (post_id,)).fetchone()

    append_post_log(post_id, "info", "Publish completed", {
        "youtube_post_url": summary.youtube_post_url,
        "youtube_post_id": summary.youtube_post_id,
    })
    return dict(final_row) if final_row is not None else {"id": post_id, "status": "published"}
