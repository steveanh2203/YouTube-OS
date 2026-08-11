"""Fast-Edit API routes — video composition, batch rename, subtitle generation."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autocapcut.services.fast_edit import (
    BatchRenamer,
    SubtitleGenerator,
    VideoComposer,
    VideoComposerError,
    video_presets_list,
    audio_presets_list,
    RenderOptions,
    SubtitleStyle,
    AnimationSettings,
    TransitionSettings,
)
from autocapcut.services.media_workspace import resolve_workspace_or_local, workspace_file_reference

_log = logging.getLogger("autocapcut.api.fast_edit")

router = APIRouter()

# Singletons (initialised lazily)
_composer: VideoComposer | None = None
_renamer: BatchRenamer | None = None
_subtitle_gen: SubtitleGenerator | None = None


def _output_reference(output_directory: str, output_path: str) -> str:
    if output_directory.startswith("workspace:"):
        return workspace_file_reference(output_directory, output_path)
    return output_path


def _file_label(path: str | None) -> str | None:
    return Path(path).name if path else None


def _get_composer() -> VideoComposer:
    global _composer
    if _composer is None:
        _composer = VideoComposer()
    return _composer


def _get_renamer() -> BatchRenamer:
    global _renamer
    if _renamer is None:
        _renamer = BatchRenamer()
    return _renamer


def _get_subtitle_gen() -> SubtitleGenerator:
    global _subtitle_gen
    if _subtitle_gen is None:
        _subtitle_gen = SubtitleGenerator()
    return _subtitle_gen


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════════════════════════════════════════


# ── System Status ─────────────────────────────────────────────────────────────

class SystemStatusResponse(BaseModel):
    ffmpeg_available: bool
    ffprobe_available: bool
    ready_to_render: bool
    whisper_available: bool


# ── Filter Presets ────────────────────────────────────────────────────────────

class FilterPresetInfo(BaseModel):
    id: str
    name: str
    description: str
    category: str


# ── Render ────────────────────────────────────────────────────────────────────

class SubtitleStyleModel(BaseModel):
    font_name: str = "Space Grotesk"
    font_size: int = 48
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = 2.0
    letter_spacing: float = 0.0
    margin_bottom: int = 60
    alignment: int = 2


class AnimationSettingsModel(BaseModel):
    type: str = "none"
    intensity: str = "medium"


class TransitionSettingsModel(BaseModel):
    type: str = "none"
    duration: float = 1.0


class RenderRequest(BaseModel):
    audio_directory: str
    image_directory: str
    output_directory: str
    subtitle_directory: Optional[str] = None
    create_individual: bool = True
    create_combined: bool = False

    # Render options
    frame_rate: float = 24.0
    resolution_width: int = 1920
    resolution_height: int = 1080
    video_codec: str = "h264"
    video_bitrate: str = "10000k"
    audio_bitrate: str = "192k"
    burn_subtitles: bool = False
    subtitle_style: Optional[SubtitleStyleModel] = None
    animation: Optional[AnimationSettingsModel] = None
    transition: Optional[TransitionSettingsModel] = None
    use_hardware_acceleration: bool = True
    keep_intermediate: bool = False
    combined_filename: str = "complete_video.mp4"
    video_filters: List[str] = Field(default_factory=list)
    audio_filters: List[str] = Field(default_factory=list)
    sync_mode: str = "standard"

    # Background music
    background_music_directory: Optional[str] = None

    # Logo
    logo_file: Optional[str] = None
    logo_enabled: bool = False
    logo_size: int = 15
    logo_opacity: int = 80
    logo_x: int = 50
    logo_y: int = 50


class RenderSceneResult(BaseModel):
    index: int
    audio_path: str
    image_path: str
    subtitle_path: Optional[str] = None
    output_path: str
    duration: float
    success: bool
    error: Optional[str] = None


class RenderResponse(BaseModel):
    ok: bool
    message: str
    scenes: List[RenderSceneResult] = Field(default_factory=list)
    combined: Optional[RenderSceneResult] = None
    total_duration: float = 0.0


# ── Batch Rename ──────────────────────────────────────────────────────────────

class BatchRenameRequest(BaseModel):
    directory: str
    asset_type: str = "audio"  # audio | image
    prefix: str = ""
    start_index: int = 1
    pad_width: int = 3
    separator: str = "_"
    lowercase_extension: bool = True


class RenameResultItem(BaseModel):
    original_path: str
    new_path: str
    success: bool
    error: Optional[str] = None


class BatchRenameResponse(BaseModel):
    ok: bool
    message: str
    results: List[RenameResultItem] = Field(default_factory=list)
    total: int = 0
    succeeded: int = 0
    failed: int = 0


class FileCountRequest(BaseModel):
    directory: str
    asset_type: str = "audio"


class FileCountResponse(BaseModel):
    total: int
    error: Optional[str] = None


# ── Subtitle Generation ──────────────────────────────────────────────────────

class SubtitleGenRequest(BaseModel):
    audio_directory: str
    subtitle_directory: str
    model_id: str = "base"
    language: Optional[str] = None
    translate_to_english: bool = False


class SubtitleResultItem(BaseModel):
    audio_path: str
    subtitle_path: Optional[str] = None
    preview_lines: List[str] = Field(default_factory=list)
    success: bool
    error: Optional[str] = None


class SubtitleGenResponse(BaseModel):
    ok: bool
    message: str
    results: List[SubtitleResultItem] = Field(default_factory=list)


class WhisperModelItem(BaseModel):
    id: str
    name: str
    size_mb: int
    recommended: bool
    available: bool


class WhisperDownloadRequest(BaseModel):
    model_id: str


class WhisperDownloadResponse(BaseModel):
    ok: bool
    message: str


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/status", response_model=SystemStatusResponse)
async def system_status() -> SystemStatusResponse:
    """Check FFmpeg / FFprobe / Whisper availability."""
    composer = _get_composer()
    status = composer.get_system_status()
    sub_gen = _get_subtitle_gen()
    return SystemStatusResponse(
        ffmpeg_available=status["ffmpeg_available"],
        ffprobe_available=status["ffprobe_available"],
        ready_to_render=status["ready_to_render"],
        whisper_available=sub_gen.is_available(),
    )


@router.get("/presets/video", response_model=List[FilterPresetInfo])
async def get_video_presets() -> List[FilterPresetInfo]:
    return [FilterPresetInfo(**p) for p in video_presets_list()]


@router.get("/presets/audio", response_model=List[FilterPresetInfo])
async def get_audio_presets() -> List[FilterPresetInfo]:
    return [FilterPresetInfo(**p) for p in audio_presets_list()]


@router.post("/render", response_model=RenderResponse)
async def render_project(req: RenderRequest) -> RenderResponse:
    """Render video from audio+image directories."""
    composer = _get_composer()

    # Build RenderOptions from request
    sub_style = SubtitleStyle()
    if req.subtitle_style:
        sub_style = SubtitleStyle(
            font_name=req.subtitle_style.font_name,
            font_size=req.subtitle_style.font_size,
            primary_color=req.subtitle_style.primary_color,
            outline_color=req.subtitle_style.outline_color,
            outline_width=req.subtitle_style.outline_width,
            letter_spacing=req.subtitle_style.letter_spacing,
            margin_bottom=req.subtitle_style.margin_bottom,
            alignment=req.subtitle_style.alignment,
        )

    anim = AnimationSettings()
    if req.animation:
        anim = AnimationSettings(type=req.animation.type, intensity=req.animation.intensity)

    trans = TransitionSettings()
    if req.transition:
        trans = TransitionSettings(type=req.transition.type, duration=req.transition.duration)

    background_music_directory = str(resolve_workspace_or_local(req.background_music_directory)) if req.background_music_directory else None
    options = RenderOptions(
        frame_rate=req.frame_rate,
        resolution=(req.resolution_width, req.resolution_height),
        video_codec=req.video_codec,
        video_bitrate=req.video_bitrate,
        audio_bitrate=req.audio_bitrate,
        burn_subtitles=req.burn_subtitles,
        subtitle_style=sub_style,
        animation=anim,
        transition=trans,
        use_hardware_acceleration=req.use_hardware_acceleration,
        keep_intermediate=req.keep_intermediate,
        combined_filename=req.combined_filename,
        video_filters=req.video_filters,
        audio_filters=req.audio_filters,
        sync_mode=req.sync_mode,
        background_music_directory=background_music_directory,
        logo_file=req.logo_file,
        logo_enabled=req.logo_enabled,
        logo_size=req.logo_size,
        logo_opacity=req.logo_opacity,
        logo_x=req.logo_x,
        logo_y=req.logo_y,
    )

    loop = asyncio.get_event_loop()
    try:
        batch = await loop.run_in_executor(
            None,
            lambda: composer.render_project(
                audio_directory=str(resolve_workspace_or_local(req.audio_directory)),
                image_directory=str(resolve_workspace_or_local(req.image_directory)),
                output_directory=str(resolve_workspace_or_local(req.output_directory)),
                subtitle_directory=str(resolve_workspace_or_local(req.subtitle_directory)) if req.subtitle_directory else None,
                options=options,
                create_individual=req.create_individual,
                create_combined=req.create_combined,
            ),
        )
    except VideoComposerError as exc:
        return RenderResponse(ok=False, message=str(exc))
    except Exception as exc:
        _log.exception("Render failed")
        return RenderResponse(ok=False, message=f"Unexpected error: {exc}")

    scenes = [
        RenderSceneResult(
            index=s.index,
            audio_path=_file_label(s.audio_path) or "",
            image_path=_file_label(s.image_path) or "",
            subtitle_path=_file_label(s.subtitle_path),
            output_path=_output_reference(req.output_directory, s.output_path),
            duration=s.duration,
            success=s.success,
            error=s.error,
        )
        for s in batch.scenes
    ]

    combined = None
    if batch.combined:
        c = batch.combined
        combined = RenderSceneResult(
            index=c.index,
            audio_path=_file_label(c.audio_path) or "",
            image_path=_file_label(c.image_path) or "",
            subtitle_path=_file_label(c.subtitle_path),
            output_path=_output_reference(req.output_directory, c.output_path),
            duration=c.duration,
            success=c.success,
            error=c.error,
        )

    total_duration = sum(s.duration for s in batch.scenes if s.success)
    succeeded = sum(1 for s in batch.scenes if s.success)
    return RenderResponse(
        ok=all(s.success for s in batch.scenes),
        message=f"Rendered {succeeded}/{len(batch.scenes)} clips" + (f" + combined video" if combined else ""),
        scenes=scenes,
        combined=combined,
        total_duration=total_duration,
    )


# ── Batch Rename ──────────────────────────────────────────────────────────────

@router.post("/rename", response_model=BatchRenameResponse)
async def batch_rename(req: BatchRenameRequest) -> BatchRenameResponse:
    renamer = _get_renamer()
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        None,
        lambda: renamer.rename_files(
            directory=str(resolve_workspace_or_local(req.directory)),
            asset_type=req.asset_type,
            prefix=req.prefix,
            start_index=req.start_index,
            pad_width=req.pad_width,
            separator=req.separator,
            lowercase_extension=req.lowercase_extension,
        ),
    )

    items = [
        RenameResultItem(
            original_path=r.original_path,
            new_path=r.new_path,
            success=r.success,
            error=r.error,
        )
        for r in results
    ]

    succeeded = sum(1 for r in results if r.success)
    failed = len(results) - succeeded
    return BatchRenameResponse(
        ok=failed == 0,
        message=f"Renamed {succeeded}/{len(results)} files",
        results=items,
        total=len(results),
        succeeded=succeeded,
        failed=failed,
    )


@router.post("/file-count", response_model=FileCountResponse)
async def file_count(req: FileCountRequest) -> FileCountResponse:
    renamer = _get_renamer()
    info = renamer.get_file_count(str(resolve_workspace_or_local(req.directory)), req.asset_type)
    return FileCountResponse(total=info["total"], error=info.get("error"))


# ── Subtitle Generation ──────────────────────────────────────────────────────

@router.get("/whisper/models", response_model=List[WhisperModelItem])
async def whisper_models() -> List[WhisperModelItem]:
    gen = _get_subtitle_gen()
    return [WhisperModelItem(**m) for m in gen.get_available_models()]


@router.post("/whisper/download", response_model=WhisperDownloadResponse)
async def whisper_download(req: WhisperDownloadRequest) -> WhisperDownloadResponse:
    gen = _get_subtitle_gen()
    if not gen.is_available():
        return WhisperDownloadResponse(ok=False, message="openai-whisper is not installed")
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, lambda: gen.download_model(req.model_id))
    return WhisperDownloadResponse(
        ok=ok,
        message=f"Model '{req.model_id}' downloaded" if ok else f"Failed to download model '{req.model_id}'",
    )


@router.post("/subtitle/generate", response_model=SubtitleGenResponse)
async def generate_subtitles(req: SubtitleGenRequest) -> SubtitleGenResponse:
    gen = _get_subtitle_gen()
    if not gen.is_available():
        return SubtitleGenResponse(ok=False, message="openai-whisper is not installed", results=[])

    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(
            None,
            lambda: gen.generate_subtitles_batch(
                audio_directory=str(resolve_workspace_or_local(req.audio_directory)),
                subtitle_directory=str(resolve_workspace_or_local(req.subtitle_directory)),
                model_id=req.model_id,
                language=req.language,
                translate_to_english=req.translate_to_english,
            ),
        )
    except Exception as exc:
        return SubtitleGenResponse(ok=False, message=f"Error: {exc}", results=[])

    items = [
        SubtitleResultItem(
            audio_path=r.audio_path,
            subtitle_path=r.subtitle_path,
            preview_lines=r.preview_lines,
            success=r.success,
            error=r.error,
        )
        for r in results
    ]

    succeeded = sum(1 for r in results if r.success)
    return SubtitleGenResponse(
        ok=all(r.success for r in results),
        message=f"Generated {succeeded}/{len(results)} subtitles",
        results=items,
    )
