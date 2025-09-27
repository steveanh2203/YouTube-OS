"""Helpers for discovering CapCut projects and mapping metadata."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List

from loguru import logger

from autocapcut.config import APP_CONFIG
from autocapcut.models import ProjectItem, ProjectSource, ProjectStatus


def discover_projects(root: Path | None = None) -> list[ProjectItem]:
    """Return CapCut projects with human-friendly names and metadata."""

    root = root or APP_CONFIG.project_root
    projects: list[ProjectItem] = []

    projects.extend(_load_local_projects(root / APP_CONFIG.local_draft_dir))
    projects.extend(_load_cloud_projects(root))

    projects.sort(
        key=lambda item: item.metadata.get("modified_ts", 0),
        reverse=True,
    )

    logger.info("Discovered %d CapCut projects", len(projects))
    return projects


def set_selection(projects: Iterable[ProjectItem], selected_names: set[str]) -> None:
    """Toggle selection state based on project names."""

    for project in projects:
        project.is_selected = project.name in selected_names
        if not project.is_selected and project.status is ProjectStatus.processing:
            project.status = ProjectStatus.pending


def _load_local_projects(root: Path) -> List[ProjectItem]:
    if not root.exists():
        logger.warning("Local draft directory not found: %s", root)
        return []

    projects: list[ProjectItem] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("."):
            continue
        if not entry.is_dir():
            continue
        meta_path = entry / "draft_meta_info.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive parsing
            logger.exception("Failed to parse %s: %s", meta_path, exc)
            continue
        name = data.get("draft_name") or entry.name
        project = ProjectItem(
            name=name,
            path=str(entry),
            source=ProjectSource.local,
            metadata={
                "modified_ts": _extract_timestamp(data.get("tm_draft_modified")),
                "draft_id": data.get("draft_id"),
            },
        )
        projects.append(project)
    return projects


def _load_cloud_projects(root: Path) -> List[ProjectItem]:
    projects: list[ProjectItem] = []
    pattern = f"{APP_CONFIG.cloud_draft_prefix}*"
    for entry in sorted(root.glob(pattern)):
        if not entry.is_dir():
            continue
        meta_path = entry / "root_meta_info.json"
        if not meta_path.exists():
            continue
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive parsing
            logger.exception("Failed to parse %s: %s", meta_path, exc)
            continue
        drafts = payload.get("all_draft_store", [])
        for draft in drafts:
            draft_fold_path = draft.get("draft_fold_path")
            if not draft_fold_path:
                continue
            folder = Path(draft_fold_path)
            if not folder.exists():
                # stale cloud record; skip but log for visibility
                logger.debug(
                    "Skipping cloud draft without folder: %s", draft_fold_path
                )
                continue
            name = draft.get("draft_name") or folder.name
            project = ProjectItem(
                name=name,
                path=str(folder),
                source=ProjectSource.cloud_cache,
                metadata={
                    "modified_ts": _extract_timestamp(
                        draft.get("tm_draft_cloud_modified")
                        or draft.get("tm_draft_modified")
                    ),
                    "draft_id": draft.get("draft_id"),
                    "cloud_entry_id": draft.get("tm_draft_cloud_entry_id"),
                    "cloud_root": str(entry),
                },
            )
            projects.append(project)
    return projects


def _extract_timestamp(raw_value: object) -> float:
    """Convert CapCut microsecond-ish timestamps to seconds for sorting."""

    if raw_value is None:
        return 0.0
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return 0.0
    # CapCut stores microseconds as large integers; normalise to seconds.
    if value > 1e12:
        return value / 1_000_000.0
    if value > 1e9:
        return value / 1_000.0
    return value
