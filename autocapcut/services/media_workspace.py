"""Managed filesystem workspace for browser-uploaded and generated media."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
from typing import BinaryIO
from uuid import uuid4


DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

MEDIA_EXTENSIONS: dict[str, set[str]] = {
    "audio": {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"},
    "image": {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"},
    "subtitle": {".srt", ".vtt"},
    "text": {".csv", ".json", ".md", ".txt"},
    "video": {".m4v", ".mkv", ".mov", ".mp4", ".webm"},
}


class MediaWorkspaceError(ValueError):
    """Base error raised for invalid managed-media operations."""


class UnsupportedMediaType(MediaWorkspaceError):
    """Raised when an uploaded file extension is not allowed."""


class UnsafeMediaPath(MediaWorkspaceError):
    """Raised when a path escapes the managed workspace."""


class MediaTooLarge(MediaWorkspaceError):
    """Raised when an upload exceeds the configured byte limit."""


@dataclass(frozen=True)
class StoredMedia:
    asset_id: str
    original_name: str
    stored_name: str
    path: str
    kind: str
    content_type: str
    size_bytes: int
    sha256: str


def workspace_root() -> Path:
    configured = os.getenv("AUTOCAPCUT_WORKSPACE_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".autocapcut" / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def media_root() -> Path:
    path = workspace_root() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_kind(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    for kind, extensions in MEDIA_EXTENSIONS.items():
        if suffix in extensions:
            return kind
    raise UnsupportedMediaType(f"Unsupported media extension: {suffix or '(none)'}")


def safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise UnsupportedMediaType("A valid filename is required.")
    return name


def resolve_managed_path(path: str | Path) -> Path:
    root = workspace_root()
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_relative_to(root):
        raise UnsafeMediaPath("Media path is outside the managed workspace.")
    return candidate


def store_upload(
    filename: str,
    source: BinaryIO,
    content_type: str | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> StoredMedia:
    original_name = safe_filename(filename)
    kind = media_kind(original_name)
    asset_id = uuid4().hex
    asset_dir = media_root() / asset_id
    asset_dir.mkdir(parents=False, exist_ok=False)
    target = asset_dir / original_name
    partial = asset_dir / f".{original_name}.uploading"
    digest = hashlib.sha256()
    size_bytes = 0

    try:
        with partial.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise MediaTooLarge(f"Upload exceeds {max_bytes} bytes.")
                digest.update(chunk)
                output.write(chunk)
        partial.replace(target)
    except Exception:
        shutil.rmtree(asset_dir, ignore_errors=True)
        raise

    return StoredMedia(
        asset_id=asset_id,
        original_name=original_name,
        stored_name=target.name,
        path=str(resolve_managed_path(target)),
        kind=kind,
        content_type=(content_type or "application/octet-stream").strip() or "application/octet-stream",
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
    )
