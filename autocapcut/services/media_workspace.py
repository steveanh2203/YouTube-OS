"""Managed filesystem workspace for browser-uploaded and generated media."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import re
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


@dataclass(frozen=True)
class WorkspaceDirectory:
    reference: str
    name: str
    created_at: float


@dataclass(frozen=True)
class WorkspaceStoredFile:
    reference: str
    name: str
    relative_path: str
    size_bytes: int


def workspace_root() -> Path:
    configured = os.getenv("AUTOCAPCUT_WORKSPACE_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".autocapcut" / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def media_root() -> Path:
    path = workspace_root() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def projects_root() -> Path:
    path = workspace_root() / "projects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_workspace_directory(name: str) -> WorkspaceDirectory:
    display_name = name.strip()
    if not display_name:
        raise UnsafeMediaPath("A workspace folder name is required.")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", display_name).strip(".-")[:80] or "project"
    directory_id = f"{slug}-{uuid4().hex[:8]}"
    path = projects_root() / directory_id
    path.mkdir(parents=False, exist_ok=False)
    return WorkspaceDirectory(reference=f"workspace:{directory_id}", name=display_name, created_at=path.stat().st_ctime)


def resolve_workspace_reference(reference: str) -> Path:
    prefix = "workspace:"
    if not reference.startswith(prefix):
        raise UnsafeMediaPath("A managed workspace reference is required.")
    directory_id = reference[len(prefix):].strip()
    if not directory_id or Path(directory_id).name != directory_id:
        raise UnsafeMediaPath("Invalid workspace reference.")
    path = (projects_root() / directory_id).resolve()
    if not path.is_relative_to(projects_root()) or not path.is_dir():
        raise UnsafeMediaPath("Workspace directory does not exist.")
    return path


def resolve_workspace_or_local(value: str | Path) -> Path:
    """Resolve a browser workspace reference while preserving legacy local paths."""
    raw_value = str(value).strip()
    if not raw_value:
        raise UnsafeMediaPath("A workspace directory is required.")
    if raw_value.startswith("workspace:"):
        return resolve_workspace_reference(raw_value)
    return Path(raw_value).expanduser().resolve()


def workspace_file_reference(directory_reference: str, file_path: str | Path) -> str:
    """Create an opaque reference for a file contained by a managed directory."""
    directory = resolve_workspace_reference(directory_reference)
    candidate = Path(file_path).expanduser().resolve()
    if not candidate.is_relative_to(directory):
        raise UnsafeMediaPath("Workspace file is outside its managed directory.")
    relative = candidate.relative_to(directory).as_posix()
    directory_id = directory_reference.removeprefix("workspace:")
    return f"workspace-file:{directory_id}/{relative}"


def resolve_workspace_file_reference(reference: str) -> Path:
    """Resolve an opaque workspace-file reference and prevent directory traversal."""
    prefix = "workspace-file:"
    if not reference.startswith(prefix):
        raise UnsafeMediaPath("A managed workspace file reference is required.")
    payload = reference[len(prefix):].strip()
    directory_id, separator, relative = payload.partition("/")
    if not separator or not relative:
        raise UnsafeMediaPath("Invalid workspace file reference.")
    directory = resolve_workspace_reference(f"workspace:{directory_id}")
    candidate = (directory / relative).resolve()
    if not candidate.is_relative_to(directory):
        raise UnsafeMediaPath("Workspace file is outside its managed directory.")
    return candidate


def store_workspace_file(
    directory_reference: str,
    relative_path: str,
    source: BinaryIO,
    *,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> WorkspaceStoredFile:
    """Store one browser-selected directory file without trusting its client path."""
    normalized = relative_path.replace("\\", "/").strip()
    relative = PurePosixPath(normalized)
    if (
        not normalized
        or relative.is_absolute()
        or ":" in relative.parts[0]
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise UnsafeMediaPath("Invalid workspace file path.")
    media_kind(relative.name)

    directory = resolve_workspace_reference(directory_reference)
    target = (directory / Path(*relative.parts)).resolve()
    if not target.is_relative_to(directory):
        raise UnsafeMediaPath("Workspace file is outside its managed directory.")
    if target.exists():
        raise UnsafeMediaPath(f"Workspace file already exists: {relative.as_posix()}")

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.uploading")
    size_bytes = 0
    try:
        with partial.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise MediaTooLarge(f"Upload exceeds {max_bytes} bytes.")
                output.write(chunk)
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    return WorkspaceStoredFile(
        reference=workspace_file_reference(directory_reference, target),
        name=target.name,
        relative_path=relative.as_posix(),
        size_bytes=size_bytes,
    )


def list_workspace_directories() -> list[WorkspaceDirectory]:
    directories: list[WorkspaceDirectory] = []
    for path in projects_root().iterdir():
        if not path.is_dir():
            continue
        directories.append(
            WorkspaceDirectory(
                reference=f"workspace:{path.name}",
                name=path.name.rsplit("-", 1)[0].replace("-", " "),
                created_at=path.stat().st_ctime,
            )
        )
    return sorted(directories, key=lambda item: item.created_at, reverse=True)


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
