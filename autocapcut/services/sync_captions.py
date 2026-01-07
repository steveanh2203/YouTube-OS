"""Synchronise image durations to match caption segments."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from loguru import logger

from autocapcut.services.draft_utils import DraftNotFoundError, load_draft, save_draft

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff"}


@dataclass
class CaptionSyncSummary:
    paired: int
    caption_total: int
    image_total: int


class SyncCaptionError(Exception):
    """Raised when caption synchronisation fails."""


def sync_project_captions(project) -> CaptionSyncSummary:
    """Adjust image segment durations to match caption segments by order."""

    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise SyncCaptionError(str(exc))

    tracks = data.get("tracks") or []
    caption_track = _select_caption_track(tracks)
    if caption_track is None:
        raise SyncCaptionError("Caption track not found")

    caption_segments = list(caption_track.get("segments") or [])
    if not caption_segments:
        raise SyncCaptionError("Caption track has no segments")

    materials = data.get("materials") or {}
    video_track = _select_main_video_track(tracks, materials)
    if video_track is None:
        raise SyncCaptionError("Video track not found")

    image_segments = _collect_image_segments(video_track, materials)
    if not image_segments:
        raise SyncCaptionError("No image segments found on video track")

    caption_segments = sorted(caption_segments, key=_segment_start)
    image_segments = sorted(image_segments, key=_segment_start)

    paired = min(len(caption_segments), len(image_segments))
    for index in range(paired):
        caption_segment = caption_segments[index]
        image_segment = image_segments[index]
        start = _segment_start(caption_segment)
        duration = _segment_duration(caption_segment)
        if index + 1 < paired:
            next_start = _segment_start(caption_segments[index + 1])
            fill_duration = next_start - start
            if fill_duration > 0:
                duration = fill_duration
        _update_segment(image_segment, start=start, duration=duration)

    _update_project_duration(data)
    save_draft(draft_path, data)

    logger.info(
        "Sync captions completed for %s | paired=%s caption_total=%s image_total=%s",
        project.name,
        paired,
        len(caption_segments),
        len(image_segments),
    )
    return CaptionSyncSummary(
        paired=paired,
        caption_total=len(caption_segments),
        image_total=len(image_segments),
    )


def _select_caption_track(tracks: Iterable[dict]) -> dict | None:
    caption_tracks = [track for track in tracks if track.get("type") in ("text", "subtitle")]
    if not caption_tracks:
        return None
    return max(caption_tracks, key=lambda track: len(track.get("segments") or []))


def _select_main_video_track(tracks: Iterable[dict], materials: dict) -> dict | None:
    video_tracks = [track for track in tracks if track.get("type") == "video"]
    if not video_tracks:
        return None

    video_materials = {item.get("id"): item for item in materials.get("videos", []) if item.get("id")}
    image_materials = {item.get("id"): item for item in materials.get("images", []) if item.get("id")}

    def image_segment_count(track: dict) -> int:
        segments = track.get("segments") or []
        count = 0
        for segment in segments:
            material_id = segment.get("material_id")
            material = video_materials.get(material_id) or image_materials.get(material_id)
            if material and _is_image_material(material):
                count += 1
        return count

    return max(video_tracks, key=image_segment_count)


def _collect_image_segments(track: dict, materials: dict) -> List[dict]:
    video_materials = {item.get("id"): item for item in materials.get("videos", []) if item.get("id")}
    image_materials = {item.get("id"): item for item in materials.get("images", []) if item.get("id")}

    segments: List[dict] = []
    for segment in track.get("segments") or []:
        material_id = segment.get("material_id")
        material = video_materials.get(material_id) or image_materials.get(material_id)
        if material and _is_image_material(material):
            segments.append(segment)
    return segments


def _is_image_material(material: dict) -> bool:
    material_type = (material.get("type") or "").lower()
    if material_type in {"photo", "image", "picture"}:
        return True
    path = material.get("path") or material.get("name") or ""
    suffix = Path(path).suffix.lower() if path else ""
    return suffix in IMAGE_EXTENSIONS


def _segment_start(segment: dict) -> int:
    timerange = segment.get("target_timerange") or {}
    return int(timerange.get("start", 0))


def _segment_duration(segment: dict) -> int:
    timerange = segment.get("target_timerange") or {}
    return int(timerange.get("duration", 0))


def _update_segment(segment: dict, *, start: int, duration: int) -> None:
    for key in ("target_timerange", "source_timerange"):
        timerange = segment.setdefault(key, {})
        timerange["start"] = start
        timerange["duration"] = duration
    render_range = segment.get("render_timerange")
    if isinstance(render_range, dict):
        render_range.setdefault("start", 0)
        render_range["duration"] = duration


def _update_project_duration(data: dict) -> None:
    max_end = 0
    for track in data.get("tracks", []) or []:
        for segment in track.get("segments", []) or []:
            timerange = segment.get("target_timerange") or {}
            start = int(timerange.get("start", 0))
            duration = int(timerange.get("duration", 0))
            max_end = max(max_end, start + duration)
    data["duration"] = max_end
