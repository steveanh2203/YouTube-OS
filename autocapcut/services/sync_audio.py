"""Audio/image synchronization helpers for CapCut draft files."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from loguru import logger

from autocapcut.models import ProjectItem
from autocapcut.services.draft_utils import DraftNotFoundError, load_draft, save_draft
from autocapcut.services.ffmpeg_utils import FFprobeError, probe_first_existing


@dataclass
class SyncSummary:
    project: ProjectItem
    updated_segments: int
    audio_duration: int


class SyncAudioError(Exception):
    """Raised when synchronization fails for a project."""


AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}


def sync_project_audio(project: ProjectItem, *, make_backup: bool = True) -> SyncSummary:
    """Synchronise video segment durations with the primary audio track.

    The function edits ``draft_content.json`` so that every video segment on the main
    track is evenly distributed across the total audio length. When FFmpeg/ffprobe is
    available and the referenced audio files are present on disk, the duration reported
    by ffprobe is used for higher accuracy; otherwise the existing timeline duration
    is taken as-is.
    """

    project_dir = Path(project.path)
    try:
        draft_path, data = load_draft(project_dir)
    except DraftNotFoundError as exc:
        raise SyncAudioError(str(exc))

    logger.info("Syncing audio for project '%s'", project.name)
    tracks = data.get("tracks") or []
    if not tracks:
        raise SyncAudioError("No tracks found in draft_content.json")

    video_track = _find_track(tracks, "video")
    audio_track = _find_track(tracks, "audio")

    if video_track is None:
        raise SyncAudioError("Video track not found")
    if audio_track is None:
        raise SyncAudioError("Audio track not found")

    video_segments = video_track.get("segments") or []
    audio_segments = audio_track.get("segments") or []

    if not video_segments:
        raise SyncAudioError("Video track has no segments")
    if not audio_segments:
        raise SyncAudioError("Audio track has no segments")

    audio_duration = _track_duration(audio_segments)

    ffmpeg_duration = _audio_duration_via_ffmpeg(
        data,
        audio_track,
        _collect_audio_files(project_dir),
        project_dir,
    )
    if ffmpeg_duration:
        audio_duration = max(audio_duration, ffmpeg_duration)

    if audio_duration <= 0:
        raise SyncAudioError("Audio track has zero duration")

    new_durations = _distribute_duration(audio_duration, len(video_segments))

    cumulative = 0
    for segment, duration in zip(video_segments, new_durations):
        _set_timerange(segment, start=cumulative, duration=duration)
        cumulative += duration

    data["duration"] = audio_duration

    save_draft(draft_path, data, make_backup=make_backup)
    return SyncSummary(project=project, updated_segments=len(video_segments), audio_duration=audio_duration)



def _find_track(tracks: Iterable[dict], track_type: str) -> dict | None:
    for track in tracks:
        if track.get("type") == track_type:
            return track
    return None


def _track_duration(segments: Iterable[dict]) -> int:
    end = 0
    for segment in segments:
        timerange = segment.get("target_timerange") or {}
        start = int(timerange.get("start", 0))
        duration = int(timerange.get("duration", 0))
        end = max(end, start + duration)
    return end


def _distribute_duration(total: int, count: int) -> List[int]:
    base = total // count
    remainder = total % count
    durations = [base] * count
    for index in range(remainder):
        durations[index] += 1
    return durations


def _set_timerange(segment: dict, *, start: int, duration: int) -> None:
    for key in ("target_timerange", "source_timerange"):
        timerange = segment.setdefault(key, {})
        timerange["start"] = start
        timerange["duration"] = duration

    if "render_timerange" in segment and isinstance(segment["render_timerange"], dict):
        segment["render_timerange"].setdefault("start", 0)
        segment["render_timerange"]["duration"] = duration




def _resolve_draft_path(project_dir: Path) -> Path | None:
    for filename in ("draft_content.json", "draft_info.json"):
        candidate = project_dir / filename
        if candidate.exists():
            return candidate
    return None

def _collect_audio_files(project_path: Path) -> List[Path]:
    files: List[Path] = []
    if not project_path.exists():
        return files
    for root, _, filenames in os.walk(project_path):
        for name in filenames:
            if Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                files.append(Path(root) / name)
    return files


def _audio_duration_via_ffmpeg(
    data: dict,
    audio_track: dict,
    audio_files: List[Path],
    project_path: Path,
) -> int | None:
    materials = (data.get("materials") or {}).get("audios") or []
    material_map: Dict[str, dict] = {
        material.get("id"): material for material in materials if material.get("id")
    }
    duration_cache: Dict[str, int] = {}

    for segment in audio_track.get("segments", []):
        material_id = segment.get("material_id")
        if not material_id or material_id in duration_cache:
            continue

        material = material_map.get(material_id)
        if material is None:
            continue

        candidates = list(_iter_material_candidates(material, project_path, audio_files))
        if not candidates:
            continue
        try:
            duration = probe_first_existing(candidates)
        except FFprobeError as exc:
            logger.debug("ffprobe error for %s: %s", material_id, exc)
            continue
        if duration:
            duration_cache[material_id] = duration

    if duration_cache:
        return max(duration_cache.values())
    return None


def _iter_material_candidates(material: dict, project_path: Path, audio_files: List[Path]):
    raw_path = material.get("path") or ""
    candidate_names = set()
    if raw_path:
        raw = Path(raw_path)
        candidate_names.add(raw.name.lower())
        if raw.exists():
            yield raw

    material_id = (material.get("id") or "").lower()

    if candidate_names:
        for file in audio_files:
            if file.name.lower() in candidate_names:
                yield file

    if material_id:
        for file in audio_files:
            if material_id in file.stem.lower():
                yield file

    # As a fallback, check Resources/audioAlg for files (CapCut often caches audio there)
    audio_alg = project_path / "Resources" / "audioAlg"
    if audio_alg.exists():
        for file in audio_alg.iterdir():
            if not file.is_file():
                continue
            if file.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            if material_id and material_id in file.stem.lower():
                yield file
            elif candidate_names and file.name.lower() in candidate_names:
                yield file
