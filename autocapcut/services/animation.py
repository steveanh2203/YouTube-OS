"""Automation helpers for applying transitions/animations."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import List
from uuid import uuid4

from loguru import logger

from autocapcut.models import ProjectItem
from autocapcut.services.animation_presets import TRANSITION_PRESETS
from autocapcut.services.draft_utils import DraftNotFoundError, load_draft, save_draft


@dataclass
class TransitionSummary:
    project: ProjectItem
    applied_transitions: int
    removed_transitions: int
    duration_microseconds: int


class TransitionError(Exception):
    """Raised when transition application fails."""


def apply_transition(
    project: ProjectItem,
    *,
    preset_key: str,
    duration_seconds: float,
    make_backup: bool = True,
) -> TransitionSummary:
    """Apply the given transition preset between all clips in the project."""

    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise TransitionError(str(exc))

    transitions = data.setdefault("materials", {}).setdefault("transitions", [])
    removed = _remove_existing_transitions(data)

    preset = TRANSITION_PRESETS.get(preset_key)
    if preset is None:
        raise TransitionError(f"Unknown transition preset: {preset_key}")

    video_track = _find_track(data.get("tracks", []), "video")
    if video_track is None:
        raise TransitionError("Video track not found")

    segments = video_track.get("segments") or []
    if len(segments) < 2:
        raise TransitionError("Need at least two segments to apply transitions")

    duration_us = int(max(duration_seconds, 0.01) * 1_000_000)

    applied = 0
    for index in range(len(segments) - 1):
        template = copy.deepcopy(preset.formatted_template())
        new_id = str(uuid4()).upper()
        template["id"] = new_id
        template["duration"] = duration_us
        template.setdefault("request_id", str(uuid4()).replace("-", ""))
        transitions.append(template)

        refs = segments[index].setdefault("extra_material_refs", [])
        refs = [ref for ref in refs if ref not in {new_id}]
        refs.append(new_id)
        segments[index]["extra_material_refs"] = refs
        applied += 1

    save_draft(draft_path, data, make_backup=make_backup)
    logger.info("Applied %d transition(s) to project %s", applied, project.name)
    return TransitionSummary(
        project=project,
        applied_transitions=applied,
        removed_transitions=removed,
        duration_microseconds=duration_us,
    )


def clear_transitions(project: ProjectItem, *, make_backup: bool = True) -> int:
    """Remove all transitions from the given project."""

    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise TransitionError(str(exc))

    removed = _remove_existing_transitions(data)
    save_draft(draft_path, data, make_backup=make_backup)
    logger.info("Removed %d transition(s) from project %s", removed, project.name)
    return removed


def _remove_existing_transitions(data: dict) -> int:
    materials = data.setdefault("materials", {})
    transitions = materials.get("transitions") or []
    if not transitions:
        return 0

    transition_ids = {entry.get("id") for entry in transitions if entry.get("id")}
    for track in data.get("tracks", []):
        for segment in track.get("segments", []):
            refs = segment.get("extra_material_refs")
            if not refs:
                continue
            refs = [ref for ref in refs if ref not in transition_ids]
            segment["extra_material_refs"] = refs

    removed = len(transitions)
    materials["transitions"] = []
    return removed


def _find_track(tracks: List[dict], track_type: str) -> dict | None:
    for track in tracks:
        if track.get("type") == track_type:
            return track
    return None
