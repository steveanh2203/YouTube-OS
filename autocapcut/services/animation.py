"""Automation helpers for applying transitions/animations."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Set
from uuid import uuid4

from loguru import logger

from autocapcut.models import ProjectItem
from autocapcut.services.animation_presets import (
    ANIMATION_PRESETS,
    EFFECT_PRESETS,
    TRANSITION_PRESETS,
)
from autocapcut.services.draft_utils import DraftNotFoundError, load_draft, save_draft


@dataclass
class TransitionSummary:
    project: ProjectItem
    applied_transitions: int
    removed_transitions: int
    duration_microseconds: int


class TransitionError(Exception):
    """Raised when transition application fails."""


@dataclass
class AnimationApplySummary:
    project: ProjectItem
    segments_updated: int
    preset_keys: List[str]
    duration_microseconds: int
    removed_existing: int


@dataclass
class AnimationRemoveSummary:
    project: ProjectItem
    removed_entries: int
    affected_segments: int


@dataclass
class EffectApplySummary:
    project: ProjectItem
    preset_keys: List[str]
    segments_added: int
    removed_existing: int


@dataclass
class EffectRemoveSummary:
    project: ProjectItem
    removed_entries: int
    removed_segments: int


class AnimationError(Exception):
    """Raised when animation application fails."""


class EffectError(Exception):
    """Raised when effect automation fails."""


AUTOCAPCUT_ANIMATION_PREFIX = "AUTOCAPCUT_ANIM"
AUTOCAPCUT_EFFECT_PREFIX = "AUTOCAPCUT_EFFECT"


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


def apply_animations(
    project: ProjectItem,
    *,
    preset_keys: Sequence[str],
    duration_seconds: float,
    make_backup: bool = True,
) -> AnimationApplySummary:
    if not preset_keys:
        raise AnimationError("No animation preset selected")

    unique_keys: List[str] = []
    for key in preset_keys:
        if key not in unique_keys:
            unique_keys.append(key)

    for key in unique_keys:
        if key not in ANIMATION_PRESETS:
            raise AnimationError(f"Unknown animation preset: {key}")

    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise AnimationError(str(exc))

    video_track = _find_track(data.get("tracks", []), "video")
    if video_track is None:
        raise AnimationError("Video track not found in draft")

    segments = video_track.get("segments") or []
    if not segments:
        raise AnimationError("No video segments available to animate")

    materials = data.setdefault("materials", {})
    removed_existing, _ = _remove_autocapcut_animations(data, keys=unique_keys)
    animation_materials = materials.setdefault("material_animations", [])

    duration_microseconds = int(duration_seconds * 1_000_000) if duration_seconds > 0 else None

    updated_segments = 0
    for segment in segments:
        segment_duration = int((segment.get("target_timerange") or {}).get("duration", 0))
        animations_payload = []
        for key in unique_keys:
            preset = ANIMATION_PRESETS[key]
            payload = preset.formatted_template()
            use_duration = duration_microseconds or preset.default_duration_us
            payload["duration"] = use_duration
            payload["start"] = _compute_animation_start(preset.category, use_duration, segment_duration)
            payload["request_id"] = f"{AUTOCAPCUT_ANIMATION_PREFIX}:{key}:{uuid4().hex.upper()}"
            animations_payload.append(payload)

        if not animations_payload:
            continue

        entry_id = str(uuid4()).upper()
        animation_materials.append(
            {
                "id": entry_id,
                "type": "sticker_animation",
                "multi_language_current": "none",
                "animations": animations_payload,
            }
        )

        refs = segment.setdefault("extra_material_refs", [])
        if entry_id not in refs:
            refs.append(entry_id)
        updated_segments += 1

    if not updated_segments:
        raise AnimationError("Animations could not be applied to any segments")

    save_draft(draft_path, data, make_backup=make_backup)
    logger.info(
        "Applied animations %s to %d segment(s) in project %s",
        ", ".join(unique_keys),
        updated_segments,
        project.name,
    )
    return AnimationApplySummary(
        project=project,
        segments_updated=updated_segments,
        preset_keys=list(unique_keys),
        duration_microseconds=duration_microseconds or 0,
        removed_existing=removed_existing,
    )


def remove_animations(
    project: ProjectItem,
    *,
    preset_keys: Sequence[str] | None = None,
    make_backup: bool = True,
) -> AnimationRemoveSummary:
    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise AnimationError(str(exc))

    unique_keys: List[str] | None = None
    if preset_keys:
        unique_keys = []
        for key in preset_keys:
            if key not in ANIMATION_PRESETS:
                logger.warning("Unknown animation preset provided for removal: %s", key)
                continue
            if key not in unique_keys:
                unique_keys.append(key)

    removed_entries, affected_segments = _remove_autocapcut_animations(data, keys=unique_keys)
    if removed_entries:
        save_draft(draft_path, data, make_backup=make_backup)
        logger.info(
            "Removed %d AutoCapcut animation entries from project %s",
            removed_entries,
            project.name,
        )
    else:
        logger.info("No matching AutoCapcut animations found in project %s", project.name)

    return AnimationRemoveSummary(
        project=project,
        removed_entries=removed_entries,
        affected_segments=affected_segments,
    )


def apply_effect(
    project: ProjectItem,
    *,
    preset_key: str,
    make_backup: bool = True,
) -> EffectApplySummary:
    if preset_key not in EFFECT_PRESETS:
        raise EffectError(f"Unknown effect preset: {preset_key}")

    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise EffectError(str(exc))

    materials = data.setdefault("materials", {})
    removed_existing, _ = _remove_autocapcut_effects(data, keys=[preset_key])
    video_effects = materials.setdefault("video_effects", [])

    preset = EFFECT_PRESETS[preset_key]
    material_payload = preset.formatted_material()
    material_id = str(uuid4()).upper()
    material_payload["id"] = material_id
    material_payload["request_id"] = f"{AUTOCAPCUT_EFFECT_PREFIX}:{preset_key}:{uuid4().hex.upper()}"
    video_effects.append(material_payload)

    segment_payload = preset.formatted_segment()
    segment_payload["id"] = str(uuid4()).upper()
    segment_payload["material_id"] = material_id

    project_duration = _infer_project_duration(data)
    segment_payload.setdefault("target_timerange", {})
    segment_payload["target_timerange"]["start"] = 0
    segment_payload["target_timerange"]["duration"] = project_duration

    effect_track = _ensure_effect_track(data)
    effect_track.setdefault("segments", []).append(segment_payload)

    save_draft(draft_path, data, make_backup=make_backup)
    logger.info("Applied effect %s to project %s", preset_key, project.name)
    return EffectApplySummary(
        project=project,
        preset_keys=[preset_key],
        segments_added=1,
        removed_existing=removed_existing,
    )


def remove_effects(
    project: ProjectItem,
    *,
    preset_keys: Sequence[str] | None = None,
    make_backup: bool = True,
) -> EffectRemoveSummary:
    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise EffectError(str(exc))

    keys: List[str] | None = None
    if preset_keys:
        keys = []
        for key in preset_keys:
            if key not in EFFECT_PRESETS:
                logger.warning("Unknown effect preset provided for removal: %s", key)
                continue
            if key not in keys:
                keys.append(key)

    removed_entries, removed_segments = _remove_autocapcut_effects(data, keys=keys)
    if removed_entries:
        save_draft(draft_path, data, make_backup=make_backup)
        logger.info(
            "Removed %d AutoCapcut effect entries from project %s",
            removed_entries,
            project.name,
        )
    else:
        logger.info("No AutoCapcut effects to remove in project %s", project.name)

    return EffectRemoveSummary(
        project=project,
        removed_entries=removed_entries,
        removed_segments=removed_segments,
    )


def _remove_existing_transitions(data: dict) -> int:
    materials = data.setdefault("materials", {})
    transitions = materials.get("transitions") or []
    if not transitions:
        return 0

    transition_ids = {entry.get("id") for entry in transitions if entry.get("id")}
    _remove_refs_from_segments(data, transition_ids)

    removed = len(transitions)
    materials["transitions"] = []
    return removed


def _find_track(tracks: List[dict], track_type: str) -> dict | None:
    for track in tracks:
        if track.get("type") == track_type:
            return track
    return None


def _remove_refs_from_segments(data: dict, material_ids: Iterable[str]) -> int:
    if not material_ids:
        return 0
    material_ids = set(material_ids)
    affected = 0
    for track in data.get("tracks", []) or []:
        for segment in track.get("segments", []) or []:
            refs = segment.get("extra_material_refs")
            if not refs:
                continue
            new_refs = [ref for ref in refs if ref not in material_ids]
            if len(new_refs) != len(refs):
                segment["extra_material_refs"] = new_refs
                affected += 1
    return affected


def _compute_animation_start(category: str, duration_us: int, segment_duration_us: int) -> int:
    if category == "out" and segment_duration_us:
        return max(0, segment_duration_us - duration_us)
    return 0


def _remove_autocapcut_animations(
    data: dict,
    *,
    keys: Sequence[str] | None,
) -> tuple[int, int]:
    materials = data.setdefault("materials", {})
    entries = materials.get("material_animations") or []
    if not entries:
        return 0, 0

    target_keys: Set[str] | None = set(keys) if keys else None
    remaining = []
    removed_ids: List[str] = []

    for entry in entries:
        animations = entry.get("animations") or []
        remove_entry = False
        for anim in animations:
            request_id = anim.get("request_id") or ""
            key = _extract_autocapcut_key(request_id, AUTOCAPCUT_ANIMATION_PREFIX)
            if key is None:
                continue
            if target_keys is None or key in target_keys:
                remove_entry = True
                break
        if remove_entry:
            removed_ids.append(entry.get("id"))
        else:
            remaining.append(entry)

    materials["material_animations"] = remaining
    affected_segments = _remove_refs_from_segments(data, removed_ids)
    return len(removed_ids), affected_segments


def _remove_autocapcut_effects(
    data: dict,
    *,
    keys: Sequence[str] | None,
) -> tuple[int, int]:
    materials = data.setdefault("materials", {})
    entries = materials.get("video_effects") or []
    if not entries:
        return 0, 0

    target_keys: Set[str] | None = set(keys) if keys else None
    remaining = []
    removed_ids: Set[str] = set()

    for entry in entries:
        request_id = entry.get("request_id") or ""
        key = _extract_autocapcut_key(request_id, AUTOCAPCUT_EFFECT_PREFIX)
        if key is None or (target_keys is not None and key not in target_keys):
            remaining.append(entry)
            continue
        removed_ids.add(entry.get("id"))

    materials["video_effects"] = remaining

    removed_segments = 0
    for track in data.get("tracks", []) or []:
        if track.get("type") != "effect":
            continue
        segments = track.get("segments") or []
        new_segments = [seg for seg in segments if seg.get("material_id") not in removed_ids]
        removed_segments += len(segments) - len(new_segments)
        track["segments"] = new_segments

    return len(removed_ids), removed_segments


def _extract_autocapcut_key(request_id: str, prefix: str) -> str | None:
    if not request_id or not request_id.startswith(prefix):
        return None
    parts = request_id.split(":")
    if len(parts) < 3:
        return None
    return parts[1]


def _ensure_effect_track(data: dict) -> dict:
    tracks = data.setdefault("tracks", [])
    existing = _find_track(tracks, "effect")
    if existing is not None:
        return existing

    track = {
        "id": str(uuid4()).upper(),
        "type": "effect",
        "segments": [],
        "attribute": 0,
        "flag": 0,
        "is_default_name": True,
        "name": "",
    }
    tracks.append(track)
    return track


def _infer_project_duration(data: dict) -> int:
    duration = int(data.get("duration") or 0)
    if duration > 0:
        return duration
    max_end = 0
    for track in data.get("tracks", []) or []:
        for segment in track.get("segments", []) or []:
            timerange = segment.get("target_timerange") or {}
            start = int(timerange.get("start", 0))
            seg_duration = int(timerange.get("duration", 0))
            max_end = max(max_end, start + seg_duration)
    return max_end
