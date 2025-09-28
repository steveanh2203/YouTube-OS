"""Helpers for discovering CapCut projects and mapping metadata."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List

from loguru import logger

from autocapcut.config import APP_CONFIG
from autocapcut.models import ProjectItem, ProjectSource, ProjectStatus
from autocapcut.services.draft_utils import resolve_draft_path
from autocapcut.services.animation_presets import (
    ANIMATION_PRESETS,
    EFFECT_PRESETS,
    TRANSITION_PRESETS,
)


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


def inspect_project(project_path: Path | str) -> dict[str, Any]:
    """Return metadata snapshot and FFmpeg feasibility for the given project."""

    folder = Path(project_path)
    if not folder.exists():
        logger.warning("Project folder missing: %s", folder)
        return {}

    meta: dict[str, Any] = {}
    try:
        stats = folder.stat()
        meta["fs_modified_ts"] = stats.st_mtime
    except FileNotFoundError:  # pragma: no cover - race
        return {}

    draft_path = resolve_draft_path(folder)
    if draft_path is None or not draft_path.exists():
        logger.warning("No draft JSON found for %s", folder)
        return meta

    try:
        data = json.loads(draft_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        logger.warning("Failed to parse %s: %s", draft_path, exc)
        return meta

    meta.update(_extract_project_metrics(data))
    ffmpeg_plan = _analyse_for_ffmpeg(folder, data)
    meta["ffmpeg"] = ffmpeg_plan
    return meta


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


def _extract_project_metrics(data: dict) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    duration_us = int(data.get("duration") or 0)
    if duration_us:
        metrics["duration_us"] = duration_us
        metrics["duration_s"] = duration_us / 1_000_000

    fps = data.get("fps")
    if fps:
        metrics["fps"] = fps

    canvas = data.get("canvas_config") or {}
    if canvas:
        metrics["canvas"] = {
            "width": canvas.get("width"),
            "height": canvas.get("height"),
        }

    tracks = data.get("tracks") or []
    metrics["track_count"] = len(tracks)
    video_segments = sum(
        len(track.get("segments") or []) for track in tracks if track.get("type") == "video"
    )
    if video_segments:
        metrics["video_segments"] = video_segments
    return metrics


def _analyse_for_ffmpeg(folder: Path, data: dict) -> dict[str, Any]:
    issues: list[str] = []
    plan: dict[str, Any] = {
        "ready": False,
        "issues": issues,
        "segments": [],
        "audio": None,
    }

    tracks = data.get("tracks") or []
    video_tracks = [track for track in tracks if track.get("type") == "video"]
    audio_tracks = [track for track in tracks if track.get("type") == "audio"]

    if len(video_tracks) != 1:
        issues.append("Require exactly one video track")
        return plan

    video_track = video_tracks[0]
    materials = data.get("materials") or {}
    video_materials = {item.get("id"): item for item in materials.get("videos", [])}
    image_materials = {item.get("id"): item for item in materials.get("images", [])}
    audio_materials = {item.get("id"): item for item in materials.get("audios", [])}
    transition_materials = {item.get("id"): item for item in materials.get("transitions", [])}
    animation_materials = {item.get("id"): item for item in materials.get("material_animations", [])}

    ignored_categories = {
        "speeds",
        "placeholder_infos",
        "canvases",
        "sound_channel_mappings",
        "material_colors",
        "loudnesses",
        "vocal_separations",
        "smart_crops",
    }
    ignored_ids = {
        entry.get("id")
        for category in ignored_categories
        for entry in materials.get(category, [])
        if isinstance(entry, dict)
    }

    allowed_transition_effects = {
        TRANSITION_PRESETS["black_fade"].template.get("effect_id")
    }
    allowed_animation_names = {preset.name for preset in ANIMATION_PRESETS.values()}

    # iterate segments
    segments_plan: list[dict[str, Any]] = []
    pending_fade_in_us = 0
    for index, segment in enumerate(video_track.get("segments") or []):
        seg_plan: dict[str, Any] = {
            "index": index,
            "fade_in_us": pending_fade_in_us,
            "fade_out_us": 0,
        }
        pending_fade_in_us = 0

        material_id = segment.get("material_id")
        material = video_materials.get(material_id) or image_materials.get(material_id)
        if not material:
            issues.append(f"Segment {index}: missing material {material_id}")
            continue

        source_path = material.get("path") or material.get("media_path")
        if not source_path:
            issues.append(f"Segment {index}: missing media path")
            continue

        resolved_path = Path(source_path)
        if not resolved_path.is_absolute():
            resolved_path = (folder / resolved_path).resolve()
        seg_plan["path"] = str(resolved_path)
        material_type = (material.get("type") or "").lower()
        seg_plan["type"] = "image" if material_type in {"photo", "image", "picture"} else "video"

        target_timerange = segment.get("target_timerange") or {}
        target_duration_us = int(target_timerange.get("duration") or 0)
        if target_duration_us <= 0:
            issues.append(f"Segment {index}: invalid duration")
            continue
        seg_plan["target_duration_us"] = target_duration_us

        source_timerange = segment.get("source_timerange") or {}
        seg_plan["source_start_us"] = int(source_timerange.get("start") or 0)
        seg_plan["source_duration_us"] = int(source_timerange.get("duration") or target_duration_us)

        refs = segment.get("extra_material_refs") or []
        for ref in refs:
            if ref in transition_materials:
                entry = transition_materials[ref]
                effect_id = entry.get("effect_id")
                if effect_id not in allowed_transition_effects:
                    issues.append(f"Segment {index}: unsupported transition {effect_id}")
                    continue
                duration_us = int(entry.get("duration") or 0)
                if duration_us > 0:
                    seg_plan["fade_out_us"] = max(seg_plan["fade_out_us"], duration_us)
                    pending_fade_in_us = max(pending_fade_in_us, duration_us)
            elif ref in animation_materials:
                entry = animation_materials[ref]
                animations = entry.get("animations") or []
                for anim in animations:
                    name = anim.get("name") or anim.get("type")
                    if name not in allowed_animation_names:
                        issues.append(f"Segment {index}: unsupported animation {name}")
                        continue
                    duration_us = int(anim.get("duration") or 0)
                    anim_type = (anim.get("type") or "").lower()
                    if anim_type == "in":
                        seg_plan["fade_in_us"] = max(seg_plan["fade_in_us"], duration_us)
                    elif anim_type == "out":
                        seg_plan["fade_out_us"] = max(seg_plan["fade_out_us"], duration_us)
            elif ref in ignored_ids:
                continue
            else:
                issues.append(f"Segment {index}: unsupported material reference {ref}")

        segments_plan.append(seg_plan)

    if not segments_plan:
        issues.append("No usable video segments")
        return plan

    plan["segments"] = segments_plan

    # audio analysis
    audio_plan: Optional[dict[str, Any]] = None
    if audio_tracks:
        if len(audio_tracks) > 1:
            issues.append("Multiple audio tracks are unsupported")
        else:
            audio_segments = audio_tracks[0].get("segments") or []
            if len(audio_segments) != 1:
                issues.append("Audio track must contain a single segment")
            else:
                audio_segment = audio_segments[0]
                material = audio_materials.get(audio_segment.get("material_id"))
                if not material:
                    issues.append("Audio material missing")
                else:
                    audio_path = Path(material.get("path") or "")
                    if not audio_path.is_absolute():
                        audio_path = (folder / audio_path).resolve()
                    audio_plan = {
                        "path": str(audio_path),
                        "start_us": int((audio_segment.get("source_timerange") or {}).get("start") or 0),
                        "duration_us": int((audio_segment.get("source_timerange") or {}).get("duration") or 0),
                    }
    plan["audio"] = audio_plan

    canvas = data.get("canvas_config") or {}
    plan["canvas"] = {
        "width": canvas.get("width", 1080),
        "height": canvas.get("height", 1920),
    }
    plan["fps"] = data.get("fps", 30)

    if not issues:
        plan["ready"] = True
    return plan
