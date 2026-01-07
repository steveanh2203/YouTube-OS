"""Synchronise image durations to match their corresponding audio clips."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from loguru import logger

from autocapcut.services.draft_utils import DraftNotFoundError, load_draft, save_draft

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff'}


@dataclass
class ImageSyncSummary:
    paired: int
    unmatched_audio: List[str]
    unmatched_images: List[str]
    audio_duration_updates: Dict[str, int]


class SyncImageError(Exception):
    """Raised when image synchronisation fails."""


def sync_project_images(project) -> ImageSyncSummary:
    """Adjust image segment durations to match audio segments with matching names."""

    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise SyncImageError(str(exc))

    materials = data.get('materials', {})
    audio_materials = {item.get('id'): item for item in materials.get('audios', []) if item.get('id')}

    # Video track segments can reference either ``materials.videos`` or ``materials.images``.
    video_materials: Dict[str, dict] = {}
    for bucket in ('videos', 'images'):
        for item in materials.get(bucket, []) or []:
            material_id = item.get('id')
            if material_id and material_id not in video_materials:
                video_materials[material_id] = item

    if not audio_materials:
        raise SyncImageError('No audio materials found in draft')
    if not video_materials:
        raise SyncImageError('No video materials found in draft')

    audio_segments = _collect_segments(data.get('tracks', []), 'audio')
    image_segments = _collect_segments(data.get('tracks', []), 'video')

    if not audio_segments:
        raise SyncImageError('No audio segments present on timeline')
    if not image_segments:
        raise SyncImageError('No video/image segments present on timeline')

    audio_map: Dict[str, dict] = {}
    for segment in audio_segments:
        material = audio_materials.get(segment.get('material_id'))
        if not material:
            continue
        key = _base_key(material)
        if key:
            audio_map[key] = segment

    image_map: Dict[str, dict] = {}
    for segment in image_segments:
        material = video_materials.get(segment.get('material_id'))
        if not material:
            continue
        if not _is_image_material(material):
            continue
        key = _base_key(material)
        if key and key not in image_map:
            image_map[key] = segment

    matched = 0
    matched_images = set()
    audio_duration_updates: Dict[str, int] = {}
    unmatched_audio: List[str] = []
    for key, audio_segment in audio_map.items():
        image_key, image_segment = _match_image_segment(key, image_map)
        if image_segment is None:
            unmatched_audio.append(key)
            continue
        duration = _segment_duration(audio_segment)
        start = _segment_start(audio_segment)
        _update_segment(image_segment, start=start, duration=duration)
        matched_images.add(image_key)
        audio_duration_updates[key] = duration
        matched += 1

    unmatched_images = [key for key in image_map.keys() if key not in matched_images]

    _update_project_duration(data)
    save_draft(draft_path, data)

    logger.info('Sync images completed for %s | matched=%s', project.name, matched)
    return ImageSyncSummary(
        paired=matched,
        unmatched_audio=unmatched_audio,
        unmatched_images=unmatched_images,
        audio_duration_updates=audio_duration_updates,
    )


def _collect_segments(tracks: List[dict], track_type: str) -> List[dict]:
    segments: List[dict] = []
    for track in tracks or []:
        if track.get('type') == track_type:
            segments.extend(track.get('segments') or [])
    return segments


def _base_key(material: dict) -> str:
    name = material.get('name') or ''
    path = material.get('path') or ''
    stem = Path(name or path).stem if (name or path) else ''
    return stem.lower()


def _is_image_material(material: dict) -> bool:
    path = material.get('path') or ''
    name = material.get('name') or ''
    target = name or path
    suffix = Path(target).suffix.lower() if target else ''
    return suffix in IMAGE_EXTENSIONS


def _segment_start(segment: dict) -> int:
    timerange = segment.get('target_timerange') or {}
    return int(timerange.get('start', 0))


def _segment_duration(segment: dict) -> int:
    timerange = segment.get('target_timerange') or {}
    return int(timerange.get('duration', 0))


def _update_segment(segment: dict, *, start: int, duration: int) -> None:
    for key in ('target_timerange', 'source_timerange'):
        timerange = segment.setdefault(key, {})
        timerange['start'] = start
        timerange['duration'] = duration
    render_range = segment.get('render_timerange')
    if isinstance(render_range, dict):
        render_range.setdefault('start', 0)
        render_range['duration'] = duration


def _update_project_duration(data: dict) -> None:
    max_end = 0
    for track in data.get('tracks', []):
        for segment in track.get('segments', []):
            timerange = segment.get('target_timerange') or {}
            start = int(timerange.get('start', 0))
            duration = int(timerange.get('duration', 0))
            max_end = max(max_end, start + duration)
    data['duration'] = max_end


def _match_image_segment(audio_key: str, image_map: Dict[str, dict]) -> Tuple[str | None, dict | None]:
    if not image_map:
        return None, None

    # Prefer exact key match first.
    if audio_key in image_map:
        return audio_key, image_map[audio_key]

    audio_norm = _normalise_name(audio_key)

    # Search by normalised form while preserving segment order.
    for key, segment in image_map.items():
        if _normalise_name(key) == audio_norm:
            return key, segment

    # Fallback: match by numeric suffix if present (e.g. _01 vs 01).
    audio_suffix = _numeric_suffix(audio_key)
    if audio_suffix is not None:
        for key, segment in image_map.items():
            if _numeric_suffix(key) == audio_suffix:
                return key, segment

    return None, None


def _normalise_name(value: str) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", "", value.lower())
    for prefix in ("audio", "voice", "sound", "image", "img", "picture", "pic", "photo"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix):]
    return cleaned


def _numeric_suffix(value: str) -> str | None:
    match = re.search(r"(\d+)$", value)
    if not match:
        return None
    number = match.group(1)
    # Normalise by removing leading zeros to make 01 == 1.
    return number.lstrip("0") or "0"
