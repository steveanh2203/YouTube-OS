"""Common helpers for working with CapCut draft files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from loguru import logger


class DraftNotFoundError(FileNotFoundError):
    """Raised when neither draft_content.json nor draft_info.json exists."""


DRAFT_FILENAMES = ("draft_content.json", "draft_info.json")


def resolve_draft_path(project_dir: Path) -> Path | None:
    """Return the first existing draft JSON path inside *project_dir*."""

    for name in DRAFT_FILENAMES:
        candidate = project_dir / name
        if candidate.exists():
            return candidate
    return None


def load_draft(project_dir: Path) -> Tuple[Path, dict]:
    """Load the CapCut draft JSON for the given project directory."""

    path = resolve_draft_path(project_dir)
    if path is None:
        raise DraftNotFoundError(f"No draft JSON found in {project_dir}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - unexpected
        raise ValueError(f"Failed to parse {path}: {exc}") from exc
    return path, data


def save_draft(path: Path, payload: dict, *, make_backup: bool = True) -> None:
    """Persist JSON payload back to *path*, optionally making a .bak backup."""

    if make_backup:
        _write_backup_if_needed(path)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    logger.debug("Saved draft %s", path)


def _write_backup_if_needed(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".bak")
    if backup_path.exists():
        return
    try:
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        logger.debug("Created backup %s", backup_path)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to create backup for %s: %s", path, exc)
