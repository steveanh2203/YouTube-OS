"""
Fast-Edit service — ported from standalone Fast-Edit PySide6 app.

Provides:
  - VideoComposer  : FFmpeg-based video rendering from audio+image+subtitle pairs
  - FilterPresets   : Reusable FFmpeg video & audio filter presets
  - BatchRenamer    : Batch rename audio/image files
  - SubtitleGenerator : Whisper-based subtitle generation (optional, requires openai-whisper)
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# Filter Presets
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FilterPreset:
    """Describes a reusable FFmpeg filter expression."""

    id: str
    name: str
    expression: str
    target: str  # "video" or "audio"
    description: str = ""
    category: str = "general"


def _ordered_dict(presets: Iterable[FilterPreset]) -> Dict[str, FilterPreset]:
    return {preset.id: preset for preset in presets}


VIDEO_FILTER_PRESETS: Dict[str, FilterPreset] = _ordered_dict(
    [
        FilterPreset(
            id="warm_tone",
            name="Warm Cinematic",
            expression="eq=contrast=1.08:brightness=0.03:saturation=1.12",
            target="video",
            description="Boosts contrast and saturation slightly for a warm cinematic look.",
            category="color",
        ),
        FilterPreset(
            id="cool_teal",
            name="Teal & Orange",
            expression="curves=preset=teal_orange",
            target="video",
            description="Classic teal/orange colour grade via curves preset.",
            category="color",
        ),
        FilterPreset(
            id="b_and_w",
            name="Black & White",
            expression="hue=s=0",
            target="video",
            description="Desaturates frame entirely for monochrome effect.",
            category="color",
        ),
        FilterPreset(
            id="sharp_pop",
            name="Sharpen",
            expression="unsharp=5:5:0.8:5:5:0.0",
            target="video",
            description="Adds crispness to details with mild sharpening.",
            category="detail",
        ),
        FilterPreset(
            id="soft_glow",
            name="Soft Glow",
            expression="gblur=sigma=8,eq=saturation=1.1:contrast=1.05",
            target="video",
            description="Applies gaussian blur with gentle contrast lift for dreamy look.",
            category="atmosphere",
        ),
        FilterPreset(
            id="vignette_focus",
            name="Vignette",
            expression="vignette=PI/5",
            target="video",
            description="Darkens corners to draw attention to centre.",
            category="atmosphere",
        ),
        FilterPreset(
            id="film_grain",
            name="Film Grain",
            expression="noise=alls=20:allf=t",
            target="video",
            description="Adds temporal grain for vintage feel.",
            category="texture",
        ),
        FilterPreset(
            id="motion_blur",
            name="Motion Blur",
            expression="tblend=all_mode='average':all_opacity=0.7",
            target="video",
            description="Blends frames for light motion blur.",
            category="motion",
        ),
    ]
)


AUDIO_FILTER_PRESETS: Dict[str, FilterPreset] = _ordered_dict(
    [
        FilterPreset(
            id="voice_clarity",
            name="Voice Clarity",
            expression="anequalizer=f=120:t=q:w=1.0:g=3,anequalizer=f=3000:t=q:w=1.5:g=4",
            target="audio",
            description="Boosts presence frequencies suited for narration/dialogue.",
            category="speech",
        ),
        FilterPreset(
            id="bass_boost",
            name="Bass Boost",
            expression="bass=g=5:f=110:w=0.4",
            target="audio",
            description="Adds low-end body to music tracks.",
            category="music",
        ),
        FilterPreset(
            id="treble_air",
            name="Airy Treble",
            expression="treble=g=4:f=6000:w=0.5",
            target="audio",
            description="Enhances high-end sparkle.",
            category="music",
        ),
        FilterPreset(
            id="broadcast_comp",
            name="Broadcast Compressor",
            expression="acompressor=threshold=-18dB:ratio=3:attack=20:release=260",
            target="audio",
            description="Smooths dynamics with gentle compression.",
            category="mastering",
        ),
        FilterPreset(
            id="loudness_norm",
            name="Loudness Normalise",
            expression="loudnorm=I=-16:LRA=11:TP=-1.5",
            target="audio",
            description="EBU R128 style loudness normalisation.",
            category="mastering",
        ),
        FilterPreset(
            id="clean_highpass",
            name="Voice High-pass",
            expression="highpass=f=80",
            target="audio",
            description="Removes low rumble for cleaner speech.",
            category="speech",
        ),
    ]
)


def video_presets_list() -> List[Dict[str, Any]]:
    return [
        {"id": p.id, "name": p.name, "description": p.description, "category": p.category}
        for p in VIDEO_FILTER_PRESETS.values()
    ]


def audio_presets_list() -> List[Dict[str, Any]]:
    return [
        {"id": p.id, "name": p.name, "description": p.description, "category": p.category}
        for p in AUDIO_FILTER_PRESETS.values()
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Batch Renamer
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class RenameResult:
    original_path: str
    new_path: str
    success: bool
    error: Optional[str] = None


class BatchRenamer:
    AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif", ".webp"}

    def rename_files(
        self,
        directory: str,
        asset_type: str = "audio",
        prefix: str = "",
        start_index: int = 1,
        pad_width: int = 3,
        separator: str = "_",
        lowercase_extension: bool = True,
    ) -> List[RenameResult]:
        results: List[RenameResult] = []
        try:
            directory_path = Path(directory)
            if not directory_path.exists():
                return [RenameResult("", "", False, f"Thu muc khong ton tai: {directory}")]

            if asset_type.lower() == "audio":
                extensions = self.AUDIO_EXTENSIONS
                default_prefix = prefix or "audio"
            elif asset_type.lower() == "image":
                extensions = self.IMAGE_EXTENSIONS
                default_prefix = prefix or "image"
            else:
                return [RenameResult("", "", False, f"Loai file khong ho tro: {asset_type}")]

            files = sorted(
                [p for p in directory_path.iterdir() if p.is_file() and p.suffix.lower() in extensions],
                key=lambda x: x.name.lower(),
            )

            current_index = start_index
            for file_path in files:
                try:
                    extension = file_path.suffix.lower() if lowercase_extension else file_path.suffix
                    padded = str(current_index).zfill(pad_width)
                    new_filename = f"{default_prefix}{separator}{padded}{extension}"
                    new_path = file_path.parent / new_filename

                    if file_path.name == new_filename:
                        results.append(RenameResult(str(file_path), str(new_path), True))
                    elif new_path.exists():
                        results.append(RenameResult(str(file_path), str(new_path), False, f"File dich da ton tai: {new_filename}"))
                    else:
                        file_path.rename(new_path)
                        results.append(RenameResult(str(file_path), str(new_path), True))
                    current_index += 1
                except Exception as e:
                    results.append(RenameResult(str(file_path), "", False, f"Loi doi ten: {e}"))
        except Exception as e:
            results.append(RenameResult("", "", False, f"Loi xu ly thu muc: {e}"))
        return results

    def get_file_count(self, directory: str, asset_type: str = "audio") -> Dict[str, Any]:
        try:
            directory_path = Path(directory)
            if not directory_path.exists():
                return {"total": 0, "error": "Thu muc khong ton tai"}
            extensions = self.AUDIO_EXTENSIONS if asset_type.lower() == "audio" else self.IMAGE_EXTENSIONS
            count = sum(1 for p in directory_path.iterdir() if p.is_file() and p.suffix.lower() in extensions)
            return {"total": count, "error": None}
        except Exception as e:
            return {"total": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Subtitle Generator (Whisper)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SubtitleResult:
    audio_path: str
    subtitle_path: Optional[str]
    preview_lines: List[str]
    success: bool
    error: Optional[str] = None


@dataclass
class WhisperModelInfo:
    id: str
    name: str
    size_mb: int
    recommended: bool = False
    available: bool = False


class SubtitleGenerator:
    """Whisper-based subtitle generation. Requires openai-whisper to be installed."""

    MODELS = [
        WhisperModelInfo("tiny", "Whisper Tiny", 75),
        WhisperModelInfo("base", "Whisper Base", 142, recommended=True),
        WhisperModelInfo("small", "Whisper Small", 465),
        WhisperModelInfo("medium", "Whisper Medium", 1500),
    ]

    SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

    def __init__(self) -> None:
        self._model_cache: Dict[str, Any] = {}
        self._whisper = None
        try:
            import whisper
            self._whisper = whisper
        except ImportError:
            pass
        self._refresh_model_availability()

    def is_available(self) -> bool:
        return self._whisper is not None

    def _refresh_model_availability(self) -> None:
        cache_dir = self._cache_directory()
        for model in self.MODELS:
            model_path = cache_dir / f"{model.id}.pt"
            model.available = model_path.exists()

    def get_available_models(self) -> List[Dict[str, Any]]:
        self._refresh_model_availability()
        return [
            {
                "id": m.id,
                "name": m.name,
                "size_mb": m.size_mb,
                "recommended": m.recommended,
                "available": m.available,
            }
            for m in self.MODELS
        ]

    def download_model(self, model_id: str) -> bool:
        if not self._whisper:
            return False
        whisper = self._whisper
        if model_id not in whisper._MODELS:
            return False
        cache_dir = self._cache_directory()
        try:
            whisper._download(whisper._MODELS[model_id], str(cache_dir), in_memory=False)
        except Exception:
            return False
        self._refresh_model_availability()
        return True

    def _get_model(self, model_id: str) -> Any:
        if not self._whisper:
            raise RuntimeError("openai-whisper is not installed")
        if model_id not in self._model_cache:
            try:
                self._model_cache[model_id] = self._whisper.load_model(model_id)
            except Exception:
                if not self.download_model(model_id):
                    raise
                self._model_cache[model_id] = self._whisper.load_model(model_id)
        return self._model_cache[model_id]

    def generate_subtitle(
        self,
        audio_path: str,
        output_path: str,
        model_id: str = "base",
        language: Optional[str] = None,
        translate_to_english: bool = False,
    ) -> SubtitleResult:
        audio_file = Path(audio_path)
        if not audio_file.exists():
            return SubtitleResult(audio_path, None, [], False, "File audio khong ton tai")
        try:
            model = self._get_model(model_id)
        except Exception as exc:
            return SubtitleResult(audio_path, None, [], False, f"Khong the tai model: {exc}")

        options: Dict[str, Any] = {"task": "translate" if translate_to_english else "transcribe", "fp16": False}
        if language:
            options["language"] = language

        try:
            result = model.transcribe(str(audio_file), **options)
        except Exception as exc:
            return SubtitleResult(audio_path, None, [], False, f"Loi Whisper: {exc}")

        segments = result.get("segments", [])
        text = result.get("text", "").strip()
        subtitle_content = self._segments_to_srt(segments) if segments else text

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(subtitle_content, encoding="utf-8")

        preview_lines = [line.strip() for line in text.splitlines() if line.strip()][:6]
        return SubtitleResult(audio_path, str(output_file), preview_lines, True)

    def generate_subtitles_batch(
        self,
        audio_directory: str,
        subtitle_directory: str,
        model_id: str = "base",
        language: Optional[str] = None,
        translate_to_english: bool = False,
    ) -> List[SubtitleResult]:
        audio_dir = Path(audio_directory)
        if not audio_dir.exists():
            return [SubtitleResult("", None, [], False, "Thu muc audio khong ton tai")]

        subtitle_dir = Path(subtitle_directory)
        subtitle_dir.mkdir(parents=True, exist_ok=True)

        audio_files = sorted(
            [p for p in audio_dir.iterdir() if p.is_file() and p.suffix.lower() in self.SUPPORTED_AUDIO_EXTENSIONS],
            key=lambda p: p.name.lower(),
        )

        results: List[SubtitleResult] = []
        for index, audio_file in enumerate(audio_files, start=1):
            subtitle_name = f"subtitle_{index:03d}.srt"
            subtitle_path = subtitle_dir / subtitle_name
            result = self.generate_subtitle(str(audio_file), str(subtitle_path), model_id=model_id, language=language, translate_to_english=translate_to_english)
            results.append(result)

        if not results:
            results.append(SubtitleResult("", None, [], False, "Khong tim thay file audio hop le"))
        return results

    def _cache_directory(self) -> Path:
        default_cache_root = Path(os.path.expanduser("~")) / ".cache"
        cache_root = Path(os.getenv("XDG_CACHE_HOME", str(default_cache_root)))
        return cache_root / "whisper"

    def _segments_to_srt(self, segments: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for idx, segment in enumerate(segments, start=1):
            start = self._format_timestamp(segment.get("start", 0.0))
            end = self._format_timestamp(segment.get("end", 0.0))
            text = (segment.get("text") or "").strip()
            lines.append(f"{idx}\n{start} --> {end}\n{text}\n")
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _format_timestamp(value: float) -> str:
        milliseconds = int(round(value * 1000))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


# ═══════════════════════════════════════════════════════════════════════════════
# Video Composer
# ═══════════════════════════════════════════════════════════════════════════════


class VideoComposerError(RuntimeError):
    """Raised when rendering fails."""


DEFAULT_RENDER_FRAME_RATE = 24.0
DEFAULT_RENDER_RESOLUTION = (1920, 1080)
DEFAULT_RENDER_VIDEO_CODEC = "h264"
DEFAULT_RENDER_VIDEO_BITRATE = "10000k"
DEFAULT_RENDER_AUDIO_BITRATE = "192k"


@dataclass
class RenderResult:
    index: int
    audio_path: str
    image_path: str
    subtitle_path: Optional[str]
    output_path: str
    duration: float
    success: bool
    error: Optional[str] = None


@dataclass
class SubtitleStyle:
    font_name: str = "Space Grotesk"
    font_size: int = 48
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = 2.0
    letter_spacing: float = 0.0
    margin_bottom: int = 60
    alignment: int = 2


@dataclass
class AnimationSettings:
    type: str = "none"  # none, zoom_in, zoom_out, ken_burns, pan_left/right/up/down
    intensity: str = "medium"  # subtle, medium, strong


@dataclass
class TransitionSettings:
    type: str = "none"
    duration: float = 1.0


@dataclass
class RenderOptions:
    frame_rate: float = DEFAULT_RENDER_FRAME_RATE
    resolution: Tuple[int, int] = DEFAULT_RENDER_RESOLUTION
    video_codec: str = DEFAULT_RENDER_VIDEO_CODEC
    video_bitrate: str = DEFAULT_RENDER_VIDEO_BITRATE
    audio_bitrate: str = DEFAULT_RENDER_AUDIO_BITRATE
    burn_subtitles: bool = False
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)
    animation: AnimationSettings = field(default_factory=AnimationSettings)
    transition: TransitionSettings = field(default_factory=TransitionSettings)
    use_hardware_acceleration: bool = True
    keep_intermediate: bool = False
    combined_filename: str = "complete_video.mp4"
    video_filters: List[str] = field(default_factory=list)
    audio_filters: List[str] = field(default_factory=list)
    sync_mode: str = "standard"  # standard | sync_audio | sync_images
    background_music_directory: Optional[str] = None
    logo_file: Optional[str] = None
    logo_enabled: bool = False
    logo_size: int = 15
    logo_opacity: int = 80
    logo_x: int = 50
    logo_y: int = 50
    logo_remove_background: bool = False


@dataclass
class RenderBatchResult:
    scenes: List[RenderResult] = field(default_factory=list)
    combined: Optional[RenderResult] = None


@dataclass
class SrtEntry:
    start: float
    end: float
    lines: List[str]


ProgressCallback = Optional[Callable[[str, float, Optional[str]], None]]


class VideoComposer:
    """High level interface for FFmpeg based rendering."""

    def __init__(self) -> None:
        self._duration_cache: Dict[str, float] = {}
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        self.ffmpeg_available = self._command_available(["ffmpeg", "-version"])
        self.ffprobe_available = self._command_available(["ffprobe", "-version"])

    @staticmethod
    def _command_available(cmd: List[str]) -> bool:
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def get_system_status(self) -> Dict[str, Any]:
        return {
            "ffmpeg_available": self.ffmpeg_available,
            "ffprobe_available": self.ffprobe_available,
            "ready_to_render": self.ffmpeg_available and self.ffprobe_available,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def render_project(
        self,
        audio_directory: str,
        image_directory: str,
        output_directory: str,
        subtitle_directory: Optional[str] = None,
        options: Optional[RenderOptions] = None,
        create_individual: bool = True,
        create_combined: bool = False,
        progress_callback: ProgressCallback = None,
    ) -> RenderBatchResult:
        if not self.ffmpeg_available:
            raise VideoComposerError("FFmpeg khong kha dung tren he thong.")
        if not self.ffprobe_available:
            raise VideoComposerError("FFprobe khong kha dung tren he thong.")

        options = options or RenderOptions()
        audio_dir = Path(audio_directory)
        image_dir = Path(image_directory)
        subtitle_dir = Path(subtitle_directory) if subtitle_directory else None
        output_dir = Path(output_directory)

        if not audio_dir.exists():
            raise VideoComposerError(f"Thu muc audio khong ton tai: {audio_directory}")
        if not image_dir.exists():
            raise VideoComposerError(f"Thu muc image khong ton tai: {image_directory}")
        output_dir.mkdir(parents=True, exist_ok=True)

        audio_files = self._find_audio_files(audio_dir)
        image_files = self._find_image_files(image_dir)

        if not audio_files:
            raise VideoComposerError("Khong tim thay file audio.")
        if not image_files:
            raise VideoComposerError("Khong tim thay file image.")

        audio_files.sort(key=lambda p: p.name.lower())
        image_files.sort(key=lambda p: p.name.lower())

        temp_dir: Optional[Path] = None
        if (options.sync_mode or "standard").lower() in {"sync_audio", "sync_images"}:
            temp_dir = Path(tempfile.mkdtemp(prefix="fast_edit_"))
        batch_result = RenderBatchResult()
        segment_plan = self._build_segment_plan(
            audio_files=audio_files,
            image_files=image_files,
            subtitle_dir=subtitle_dir,
            options=options,
            temp_dir=temp_dir,
        )
        if not segment_plan:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise VideoComposerError("Khong co cap audio/image hop le.")

        total_steps = len(segment_plan) + (1 if create_combined else 0)
        completed_steps = 0

        try:
            scene_outputs: List[Path] = []
            scene_durations: List[float] = []

            if create_individual or create_combined:
                for index, plan in enumerate(segment_plan, start=1):
                    duration_hint = self._probe_duration(plan["audio"]) if create_combined else None
                    if progress_callback:
                        progress_callback("scene", completed_steps / max(total_steps, 1), f"Rendering clip {index}/{len(segment_plan)}")
                    result = self._render_scene(
                        index=index,
                        audio_file=plan["audio"],
                        image_file=plan["image"],
                        subtitle_file=plan.get("subtitle"),
                        output_dir=output_dir,
                        temp_dir=temp_dir,
                        options=options,
                        duration_hint=duration_hint,
                    )
                    batch_result.scenes.append(result)
                    if result.success:
                        scene_outputs.append(Path(result.output_path))
                        scene_durations.append(result.duration or duration_hint or 0.0)
                    else:
                        raise VideoComposerError(result.error or "Render that bai")
                    completed_steps += 1
                    if progress_callback:
                        progress_callback("scene", completed_steps / max(total_steps, 1), f"Hoan thanh clip {index}")

            if create_combined and scene_outputs:
                if progress_callback:
                    progress_callback("combined", completed_steps / max(total_steps, 1), "Dang ghep video hoan chinh")
                combined_result = self._render_combined(
                    clips=scene_outputs,
                    durations=scene_durations,
                    output_dir=output_dir,
                    options=options,
                    reuse_single_clip=not create_individual,
                )
                batch_result.combined = combined_result
                completed_steps += 1
                if progress_callback:
                    progress_callback("combined", completed_steps / max(total_steps, 1), "Hoan thanh video ghep")

            if not options.keep_intermediate:
                self._cleanup_temp_outputs(scene_outputs, keep=create_individual)
                if temp_dir is not None:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                if temp_dir is not None:
                    self._write_manifest(temp_dir, scene_outputs)

        except Exception:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise

        return batch_result

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------
    def _find_audio_files(self, directory: Path) -> List[Path]:
        return [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}]

    def _find_image_files(self, directory: Path) -> List[Path]:
        return [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif", ".webp"}]

    # ------------------------------------------------------------------
    # Segment planning
    # ------------------------------------------------------------------
    def _build_segment_plan(
        self,
        audio_files: List[Path],
        image_files: List[Path],
        subtitle_dir: Optional[Path],
        options: RenderOptions,
        temp_dir: Optional[Path],
    ) -> List[Dict[str, Path]]:
        subtitle_lookup = self._build_subtitle_lookup(subtitle_dir)
        mode = (options.sync_mode or "standard").lower()
        subtitle_order = self._ordered_subtitle_list(subtitle_lookup)

        if mode == "sync_images":
            return self._build_plan_sync_images(audio_files, image_files, subtitle_lookup, subtitle_order, temp_dir)
        if mode == "sync_audio":
            return self._build_plan_sync_audio(audio_files, image_files, subtitle_lookup, subtitle_order, temp_dir)
        return self._build_plan_standard(audio_files, image_files, subtitle_lookup, subtitle_order)

    def _build_plan_standard(self, audio_files, image_files, subtitle_lookup, subtitle_order):
        if not audio_files:
            return []
        pairs = []
        for index in range(min(len(audio_files), len(image_files))):
            plan_item: Dict[str, Path] = {"audio": audio_files[index], "image": image_files[index]}
            sub = self._subtitle_for_audio(index, audio_files[index], subtitle_lookup, subtitle_order)
            if sub:
                plan_item["subtitle"] = sub
            pairs.append(plan_item)
        return pairs

    def _build_plan_sync_images(self, audio_files, image_files, subtitle_lookup, subtitle_order, temp_dir):
        if not image_files or not audio_files:
            return []
        if temp_dir is None:
            raise VideoComposerError("Thieu thu muc tam cho che do sync_images.")

        plan = []
        group_size = max(1, math.ceil(len(audio_files) / len(image_files)))
        audio_index = 0

        for idx, image_file in enumerate(image_files):
            grouped_audio = audio_files[audio_index: audio_index + group_size]
            audio_index += len(grouped_audio)
            if not grouped_audio:
                break

            if len(grouped_audio) == 1:
                audio_path = grouped_audio[0]
            else:
                concat_path = temp_dir / f"sync_img_audio_{idx:03d}.wav"
                self._concat_audio_files(grouped_audio, concat_path)
                audio_path = concat_path

            subtitle_file = None
            combined_path = temp_dir / f"sync_img_sub_{idx:03d}.srt"
            combined = self._combine_group_subtitles(grouped_audio, subtitle_lookup, subtitle_order, combined_path, audio_index - len(grouped_audio))
            if combined:
                subtitle_file = combined
            if subtitle_file is None and subtitle_order:
                subtitle_file = subtitle_order[idx % len(subtitle_order)]

            plan_item: Dict[str, Path] = {"audio": audio_path, "image": image_file}
            if subtitle_file:
                plan_item["subtitle"] = subtitle_file
            plan.append(plan_item)
            if audio_index >= len(audio_files):
                break
        return plan

    def _build_plan_sync_audio(self, audio_files, image_files, subtitle_lookup, subtitle_order, temp_dir):
        if not image_files or not audio_files:
            return []
        if temp_dir is None:
            raise VideoComposerError("Thieu thu muc tam cho che do sync_audio.")

        if len(audio_files) == 1:
            full_audio_path = audio_files[0]
        else:
            full_audio_path = temp_dir / "sync_audio_full.wav"
            self._concat_audio_files(audio_files, full_audio_path)

        total_duration = self._probe_duration(full_audio_path)
        if total_duration <= 0:
            return []

        image_count = len(image_files)
        base_duration = total_duration / image_count
        plan = []
        current_start = 0.0
        timeline_entries = self._collect_timeline_entries(audio_files, subtitle_lookup, subtitle_order)

        for idx, image_file in enumerate(image_files):
            segment_duration = max(total_duration - current_start, 0.0) if idx == image_count - 1 else base_duration
            if segment_duration <= 0:
                continue

            trimmed_audio = temp_dir / f"sync_audio_segment_{idx:03d}.wav"
            self._extract_audio_segment(full_audio_path, current_start, segment_duration, trimmed_audio)
            plan_item: Dict[str, Path] = {"audio": trimmed_audio, "image": image_file}

            subtitle_file = None
            if timeline_entries:
                slice_path = temp_dir / f"sync_audio_sub_{idx:03d}.srt"
                if self._write_srt_slice(timeline_entries, current_start, current_start + segment_duration, slice_path):
                    subtitle_file = slice_path
            if subtitle_file is None and subtitle_order:
                subtitle_file = subtitle_order[idx % len(subtitle_order)]
            if subtitle_file:
                plan_item["subtitle"] = subtitle_file

            plan.append(plan_item)
            current_start += segment_duration
        return plan

    # ------------------------------------------------------------------
    # Subtitle helpers
    # ------------------------------------------------------------------
    def _build_subtitle_lookup(self, subtitle_dir: Optional[Path]) -> Dict[str, Path]:
        lookup: Dict[str, Path] = {}
        if subtitle_dir and subtitle_dir.exists():
            for path in subtitle_dir.glob("*.srt"):
                lookup[path.stem.lower()] = path
        return lookup

    def _ordered_subtitle_list(self, subtitle_lookup: Dict[str, Path]) -> List[Path]:
        return sorted(subtitle_lookup.values(), key=lambda p: p.name.lower()) if subtitle_lookup else []

    def _subtitle_for_audio(self, index, audio_file, subtitle_lookup, subtitle_order):
        if subtitle_lookup:
            match = subtitle_lookup.get(audio_file.stem.lower())
            if match:
                return match
        if subtitle_order:
            return subtitle_order[index % len(subtitle_order)]
        return None

    def _combine_group_subtitles(self, audio_group, subtitle_lookup, subtitle_order, destination, start_index):
        entries: List[SrtEntry] = []
        offset = 0.0
        for idx, audio_file in enumerate(audio_group):
            srt_path = self._subtitle_for_audio(start_index + idx, audio_file, subtitle_lookup, subtitle_order)
            if srt_path and srt_path.exists():
                parsed = self._parse_srt_entries(srt_path)
                entries.extend(SrtEntry(e.start + offset, e.end + offset, e.lines) for e in parsed)
            offset += self._probe_duration(audio_file)
        if not entries:
            return None
        entries.sort(key=lambda e: e.start)
        self._write_srt_entries(entries, destination)
        return destination

    def _collect_timeline_entries(self, audio_files, subtitle_lookup, subtitle_order):
        timeline: List[SrtEntry] = []
        offset = 0.0
        for index, audio_file in enumerate(audio_files):
            srt_path = self._subtitle_for_audio(index, audio_file, subtitle_lookup, subtitle_order)
            if srt_path and srt_path.exists():
                parsed = self._parse_srt_entries(srt_path)
                timeline.extend(SrtEntry(e.start + offset, e.end + offset, e.lines) for e in parsed)
            offset += self._probe_duration(audio_file)
        timeline.sort(key=lambda e: e.start)
        return timeline

    def _write_srt_slice(self, entries, start, end, destination):
        slice_entries = []
        for entry in entries:
            if entry.end <= start or entry.start >= end:
                continue
            new_start = max(entry.start, start) - start
            new_end = min(entry.end, end) - start
            if new_end <= new_start:
                continue
            slice_entries.append(SrtEntry(new_start, new_end, entry.lines))
        if not slice_entries:
            return False
        self._write_srt_entries(slice_entries, destination)
        return True

    def _write_srt_entries(self, entries, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        entries_sorted = sorted(entries, key=lambda e: e.start)
        with destination.open("w", encoding="utf-8") as handle:
            for index, entry in enumerate(entries_sorted, start=1):
                start_str = self._seconds_to_srt(max(entry.start, 0.0))
                end_str = self._seconds_to_srt(max(entry.end, 0.0))
                handle.write(f"{index}\n{start_str} --> {end_str}\n")
                for line in entry.lines:
                    handle.write(f"{line}\n")
                handle.write("\n")

    def _parse_srt_entries(self, path: Path) -> List[SrtEntry]:
        entries: List[SrtEntry] = []
        if not path.exists():
            return entries
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return entries
        raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return entries
        blocks = re.split(r"\n\s*\n", raw)
        for block in blocks:
            lines = [line for line in block.split("\n") if line.strip() != "" or line == ""]
            if not lines:
                continue
            lines = [line.strip("\ufeff") for line in lines]
            if re.match(r"^\d+$", lines[0].strip()):
                lines = lines[1:]
            if not lines:
                continue
            timing_line = lines[0].strip()
            if "-->" not in timing_line:
                continue
            start_text, end_text = [part.strip() for part in timing_line.split("-->")]
            try:
                start_sec = self._srt_time_to_seconds(start_text)
                end_sec = self._srt_time_to_seconds(end_text)
            except ValueError:
                continue
            text_lines = lines[1:] or [""]
            entries.append(SrtEntry(start=start_sec, end=end_sec, lines=text_lines))
        return entries

    def _srt_time_to_seconds(self, value: str) -> float:
        value = value.strip().replace(",", ".")
        match = re.match(r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{1,3})", value)
        if not match:
            raise ValueError("Invalid SRT timestamp")
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        milliseconds = int(match.group(4).ljust(3, "0"))
        return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0

    def _seconds_to_srt(self, value: float) -> str:
        value = max(value, 0.0)
        hours = int(value // 3600)
        minutes = int((value % 3600) // 60)
        seconds = int(value % 60)
        milliseconds = int(round((value - int(value)) * 1000))
        if milliseconds >= 1000:
            milliseconds -= 1000
            seconds += 1
        if seconds >= 60:
            seconds -= 60
            minutes += 1
        if minutes >= 60:
            minutes -= 60
            hours += 1
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    # ------------------------------------------------------------------
    # Audio helpers
    # ------------------------------------------------------------------
    def _concat_audio_files(self, audio_paths: List[Path], output_path: Path) -> None:
        if len(audio_paths) == 1:
            shutil.copy(audio_paths[0], output_path)
            return
        list_path = output_path.with_suffix(".txt")
        with list_path.open("w", encoding="utf-8") as handle:
            for path in audio_paths:
                handle.write(f"file '{path.as_posix()}'\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "pcm_s16le", str(output_path)]
        process = subprocess.run(cmd, capture_output=True, text=True)
        list_path.unlink(missing_ok=True)
        if process.returncode != 0:
            raise VideoComposerError(process.stderr.strip() or "Khong the noi file audio.")

    def _extract_audio_segment(self, source: Path, start: float, duration: float, destination: Path) -> None:
        cmd = ["ffmpeg", "-y", "-ss", f"{max(start, 0.0):.6f}", "-t", f"{max(duration, 0.0):.6f}", "-i", str(source), "-acodec", "pcm_s16le", str(destination)]
        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise VideoComposerError(process.stderr.strip() or "Khong the cat doan audio.")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_scene(
        self,
        index: int,
        audio_file: Path,
        image_file: Path,
        subtitle_file: Optional[Path],
        output_dir: Path,
        temp_dir: Optional[Path],
        options: RenderOptions,
        duration_hint: Optional[float] = None,
    ) -> RenderResult:
        output_path = output_dir / f"clip_{index:03d}.mp4"
        temp_output_parent = temp_dir or output_dir
        temp_output = temp_output_parent / f"clip_{index:03d}.partial.mp4"

        if self._can_use_fast_render_path(options, subtitle_file):
            return self._render_scene_fast(index, audio_file, image_file, subtitle_file, output_path, temp_output, options, duration_hint)

        duration = duration_hint if duration_hint is not None else self._probe_duration(audio_file)
        if duration <= 0:
            return RenderResult(index, str(audio_file), str(image_file), str(subtitle_file) if subtitle_file else None, "", 0.0, False, "Khong xac dinh duoc thoi luong audio")

        cmd: List[str] = ["ffmpeg", "-y", "-loop", "1", "-framerate", f"{options.frame_rate}", "-i", str(image_file), "-i", str(audio_file)]
        current_input_index = 2
        logo_input_index = None
        music_input_index = None

        if options.logo_enabled and options.logo_file and Path(options.logo_file).exists():
            cmd.extend(["-i", str(options.logo_file)])
            logo_input_index = current_input_index
            current_input_index += 1

        background_music_file = None
        if options.background_music_directory:
            background_music_file = self._get_background_music(options.background_music_directory, duration)
            if background_music_file:
                cmd.extend(["-i", str(background_music_file)])
                music_input_index = current_input_index
                current_input_index += 1

        input_sub_index = None
        if subtitle_file and not options.burn_subtitles:
            cmd.extend(["-i", str(subtitle_file)])
            input_sub_index = current_input_index
            current_input_index += 1

        cmd.extend(["-t", f"{duration:.4f}", "-r", f"{options.frame_rate}"])

        video_steps = self._video_filter_steps(options, duration, options.frame_rate, options.resolution)
        audio_chain = self._audio_filter_chain(options)

        has_logo = logo_input_index is not None
        has_background_music = music_input_index is not None
        needs_complex_filter = has_logo or has_background_music or (options.burn_subtitles and subtitle_file)

        if needs_complex_filter:
            filter_parts = []
            base_chain = ",".join(step for step in video_steps if step)
            video_label_index = 0
            filter_parts.append(f"[0:v]{base_chain}[v{video_label_index}]")
            video_stream = f"[v{video_label_index}]"

            if has_logo:
                logo_preprocessing = self._build_logo_preprocessing(options, logo_input_index)
                overlay_source = f"[{logo_input_index}:v]"
                if logo_preprocessing:
                    filter_parts.append(f"[{logo_input_index}:v]{logo_preprocessing}[logo_pre]")
                    overlay_source = "[logo_pre]"
                video_label_index += 1
                next_video_stream = f"[v{video_label_index}]"
                filter_parts.append(f"{video_stream}{overlay_source}overlay={options.logo_x}:{options.logo_y}{next_video_stream}")
                video_stream = next_video_stream

            if options.burn_subtitles and subtitle_file:
                subtitle_filter = self._build_subtitle_filter(subtitle_file, options.subtitle_style)
                video_label_index += 1
                next_video_stream = f"[v{video_label_index}]"
                filter_parts.append(f"{video_stream}{subtitle_filter}{next_video_stream}")
                video_stream = next_video_stream

            video_label_index += 1
            final_video_stream = f"[v{video_label_index}]"
            filter_parts.append(f"{video_stream}format=yuv420p{final_video_stream}")
            video_stream = final_video_stream

            audio_stream = "1:a"
            audio_label_index = 0
            if has_background_music:
                music_duration = self._probe_duration(background_music_file)
                audio_mix_filter = self._build_audio_mix_filter(music_input_index, duration, music_duration)
                if audio_mix_filter:
                    filter_parts.append(audio_mix_filter)
                    audio_stream = "[aout]"

            if audio_chain:
                for expression in audio_chain:
                    audio_label_index += 1
                    target_label = f"[a{audio_label_index}]"
                    source_label = audio_stream if audio_stream.startswith("[") else f"[{audio_stream}]"
                    filter_parts.append(f"{source_label}{expression}{target_label}")
                    audio_stream = target_label

            complex_filter = ";".join(filter_parts)
            cmd.extend(["-filter_complex", complex_filter])
            cmd.extend(["-map", video_stream if video_stream.startswith("[") else f"[{video_stream}]"])
            cmd.extend(["-map", audio_stream if audio_stream.startswith("[") else audio_stream])
        else:
            vf_parts = list(video_steps) + ["format=yuv420p"]
            video_filter = ",".join(part for part in vf_parts if part)
            cmd.extend(["-vf", video_filter])
            if audio_chain:
                cmd.extend(["-af", ",".join(audio_chain)])
            cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])

        cmd.extend(self._video_encoder_args(options))
        cmd.extend(["-c:a", "aac", "-b:a", options.audio_bitrate])

        if input_sub_index is not None:
            cmd.extend(["-c:s", "mov_text", "-map", f"{input_sub_index}:0"])

        cmd.extend(["-shortest", str(temp_output)])

        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            return RenderResult(index, str(audio_file), str(image_file), str(subtitle_file) if subtitle_file else None, str(temp_output), duration, False, process.stderr.strip() or "FFmpeg render failed")

        shutil.move(str(temp_output), str(output_path))
        return RenderResult(index, str(audio_file), str(image_file), str(subtitle_file) if subtitle_file else None, str(output_path), duration, True)

    def _can_use_fast_render_path(self, options: RenderOptions, subtitle_file: Optional[Path]) -> bool:
        animation_type = (options.animation.type or "none").lower()
        has_logo = bool(options.logo_enabled and options.logo_file and Path(options.logo_file).exists())
        has_background_music = bool(options.background_music_directory)
        return animation_type == "none" and not has_logo and not has_background_music and not options.burn_subtitles

    def _render_scene_fast(self, index, audio_file, image_file, subtitle_file, output_path, temp_output, options, duration_hint):
        cmd: List[str] = ["ffmpeg", "-y", "-loop", "1", "-framerate", f"{options.frame_rate}", "-i", str(image_file), "-i", str(audio_file)]
        subtitle_input_index = None
        if subtitle_file:
            cmd.extend(["-i", str(subtitle_file)])
            subtitle_input_index = 2

        video_filter = self._simple_video_filter(options)
        if video_filter:
            cmd.extend(["-vf", video_filter])
        audio_chain = self._audio_filter_chain(options)
        if audio_chain:
            cmd.extend(["-af", ",".join(audio_chain)])

        cmd.extend(["-r", f"{options.frame_rate}", "-map", "0:v:0", "-map", "1:a:0"])
        cmd.extend(self._video_encoder_args(options))
        cmd.extend(["-c:a", "aac", "-b:a", options.audio_bitrate])

        if subtitle_input_index is not None:
            cmd.extend(["-c:s", "mov_text", "-map", f"{subtitle_input_index}:0"])

        cmd.extend(["-shortest", str(temp_output)])
        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            temp_output.unlink(missing_ok=True)
            return RenderResult(index, str(audio_file), str(image_file), str(subtitle_file) if subtitle_file else None, str(temp_output), 0.0, False, process.stderr.strip() or "FFmpeg render failed")

        shutil.move(str(temp_output), str(output_path))
        duration = duration_hint if duration_hint is not None else 0.0
        return RenderResult(index, str(audio_file), str(image_file), str(subtitle_file) if subtitle_file else None, str(output_path), duration, True)

    def _render_combined(self, clips, durations, output_dir, options, reuse_single_clip=False):
        if not clips:
            raise VideoComposerError("Khong co clip de ghep.")

        combined_path = output_dir / (options.combined_filename or "complete_video.mp4")
        if len(clips) == 1:
            source_clip = clips[0]
            if reuse_single_clip and source_clip != combined_path:
                combined_path.unlink(missing_ok=True)
                shutil.move(str(source_clip), str(combined_path))
            elif source_clip != combined_path:
                shutil.copy2(str(source_clip), str(combined_path))
            total_duration = durations[0] if durations else 0.0
            return RenderResult(0, "", "", None, str(combined_path), total_duration, True)

        return self._combine_without_transition(clips, durations, combined_path)

    def _combine_without_transition(self, clips, durations, output_path):
        concat_file = output_path.with_suffix(".txt")
        with concat_file.open("w", encoding="utf-8") as handle:
            for clip in clips:
                handle.write(f"file '{clip.as_posix()}'\n")

        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)]
        process = subprocess.run(cmd, capture_output=True, text=True)
        concat_file.unlink(missing_ok=True)
        if process.returncode != 0:
            raise VideoComposerError(process.stderr.strip() or "Ghep video that bai")

        total_duration = sum(durations)
        return RenderResult(0, "", "", None, str(output_path), total_duration, True)

    # ------------------------------------------------------------------
    # FFmpeg probe
    # ------------------------------------------------------------------
    def _probe_duration(self, audio_file: Path) -> float:
        cache_key = str(audio_file)
        cached = self._duration_cache.get(cache_key)
        if cached is not None:
            return cached
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_file)]
        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            return 0.0
        try:
            duration = float(process.stdout.strip())
        except ValueError:
            return 0.0
        self._duration_cache[cache_key] = duration
        return duration

    # ------------------------------------------------------------------
    # Video filters
    # ------------------------------------------------------------------
    def _base_video_filter(self, resolution: Tuple[int, int]) -> str:
        if resolution == DEFAULT_RENDER_RESOLUTION:
            return "setsar=1"
        width, height = resolution
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"

    def _simple_video_filter(self, options: RenderOptions) -> str:
        steps: List[str] = []
        if options.resolution != DEFAULT_RENDER_RESOLUTION:
            steps.append(self._base_video_filter(options.resolution))
        for filter_id in options.video_filters:
            preset = VIDEO_FILTER_PRESETS.get(filter_id)
            if preset and preset.target == "video":
                steps.append(preset.expression)
        return ",".join(step for step in steps if step)

    def _build_animation_filter(self, animation: AnimationSettings, duration: float, frame_rate: float, resolution: Tuple[int, int]) -> str:
        width, height = resolution
        frames = max(int(math.ceil(duration * frame_rate)), 1)
        base = self._base_video_filter(resolution)
        anim_type = (animation.type or "none").lower()
        intensity = (animation.intensity or "medium").lower()
        step_map = {"subtle": 0.0004, "medium": 0.0008, "strong": 0.0014}
        step = step_map.get(intensity, 0.0008)

        if anim_type == "zoom_in":
            zoom_expr = f"'min(1.0+{step}*on,1.5)'"
            return f"{base},zoompan=z={zoom_expr}:d={frames}:s={width}x{height}:fps={frame_rate},setsar=1"
        if anim_type == "zoom_out":
            start_zoom = 1.3 if intensity == "strong" else 1.2 if intensity == "medium" else 1.1
            zoom_expr = f"'max({start_zoom}-{step}*on,1.0)'"
            return f"{base},zoompan=z={zoom_expr}:d={frames}:s={width}x{height}:fps={frame_rate},setsar=1"
        if anim_type == "ken_burns":
            zoom_expr = f"'min(1.0+{step}*on,1.25)'"
            x_expr = "'(iw-iw/zoom)/2'"
            y_expr = "'(ih-ih/zoom)/2'"
            return f"{base},zoompan=z={zoom_expr}:x={x_expr}:y={y_expr}:d={frames}:s={width}x{height}:fps={frame_rate},setsar=1"
        if anim_type in {"pan_left", "pan_right", "pan_up", "pan_down"}:
            divisor = max(frames - 1, 1)
            x_expr = "'0'"
            y_expr = "'0'"
            if anim_type == "pan_left":
                x_expr = f"'(iw-ow)*on/{divisor}'"
            elif anim_type == "pan_right":
                x_expr = f"'(iw-ow)*(1-on/{divisor})'"
            elif anim_type == "pan_up":
                y_expr = f"'(ih-oh)*(1-on/{divisor})'"
            elif anim_type == "pan_down":
                y_expr = f"'(ih-oh)*on/{divisor}'"
            return f"{base},zoompan=z='1.0':x={x_expr}:y={y_expr}:d={frames}:s={width}x{height}:fps={frame_rate},setsar=1"
        return base

    def _build_subtitle_filter(self, subtitle_file: Path, style: SubtitleStyle) -> str:
        subtitle_path = str(subtitle_file).replace("\\", "/").replace(":", r"\\:")
        primary = self._hex_to_bgr(style.primary_color)
        outline = self._hex_to_bgr(style.outline_color)
        force_style = (
            f"FontName={style.font_name},FontSize={style.font_size},PrimaryColour={primary},"
            f"OutlineColour={outline},Outline={style.outline_width},Spacing={style.letter_spacing},"
            f"MarginV={style.margin_bottom},Alignment={style.alignment}"
        )
        return f"subtitles='{subtitle_path}':force_style='{force_style}'"

    def _video_encoder_args(self, options: RenderOptions) -> List[str]:
        hw = options.use_hardware_acceleration
        codec = options.video_codec.lower()
        if hw:
            encoder = "hevc_videotoolbox" if codec == "hevc" else "h264_videotoolbox"
            return ["-c:v", encoder, "-realtime", "1", "-prio_speed", "1", "-allow_sw", "1", "-b:v", options.video_bitrate, "-pix_fmt", "yuv420p"]
        encoder = "libx265" if codec == "hevc" else "libx264"
        args = ["-c:v", encoder, "-preset", "superfast" if codec == "hevc" else "veryfast", "-b:v", options.video_bitrate, "-pix_fmt", "yuv420p"]
        if codec == "h264":
            args.extend(["-tune", "stillimage"])
        return args

    def _hex_to_bgr(self, hex_color: str) -> str:
        value = hex_color.lstrip("#")
        if len(value) != 6:
            value = "FFFFFF"
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
        return f"&H00{b:02X}{g:02X}{r:02X}"

    def _cleanup_temp_outputs(self, clips: List[Path], keep: bool) -> None:
        if keep:
            return
        for clip in clips:
            clip.unlink(missing_ok=True)

    def _write_manifest(self, temp_dir: Path, clips: List[Path]) -> None:
        manifest = {"clips": [str(path) for path in clips], "note": "clip files preserved for debugging"}
        with (temp_dir / "manifest.json").open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)

    def _get_background_music(self, music_directory: str, target_duration: float) -> Optional[Path]:
        music_dir = Path(music_directory)
        if not music_dir.exists():
            return None
        music_files = [p for p in music_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}]
        if not music_files:
            return None
        music_file = music_files[0]
        music_duration = self._probe_duration(music_file)
        if music_duration <= 0:
            return None
        return music_file

    def _build_logo_preprocessing(self, options: RenderOptions, logo_input_index: int) -> Optional[str]:
        logo_scale = options.logo_size / 100.0
        opacity = options.logo_opacity / 100.0
        logo_filters = []
        if logo_scale != 1.0:
            logo_filters.append(f"scale=iw*{logo_scale}:ih*{logo_scale}")
        if opacity < 1.0:
            logo_filters.append(f"format=rgba,colorchannelmixer=aa={opacity}")
        return ",".join(logo_filters) if logo_filters else None

    def _build_audio_mix_filter(self, music_input_index: int, target_duration: float, music_duration: float) -> str:
        if music_duration > 0 and music_duration < target_duration:
            loops_needed = int(target_duration / music_duration) + 1
            sample_rate = 48000
            loop_size = int(sample_rate * music_duration)
            music_filter = f"[{music_input_index}:a]aloop=loop={loops_needed}:size={loop_size}[bgm]"
            mix_filter = "[1:a][bgm]amix=inputs=2:duration=first:weights='1 0.3'[aout]"
            return f"{music_filter};{mix_filter}"
        else:
            return f"[1:a][{music_input_index}:a]amix=inputs=2:duration=first:weights='1 0.3'[aout]"

    def _video_filter_steps(self, options, duration, frame_rate, resolution):
        steps = [self._build_animation_filter(options.animation, duration, frame_rate, resolution)]
        for filter_id in options.video_filters:
            preset = VIDEO_FILTER_PRESETS.get(filter_id)
            if preset and preset.target == "video":
                steps.append(preset.expression)
        return [step for step in steps if step]

    def _audio_filter_chain(self, options):
        chain: List[str] = []
        for filter_id in options.audio_filters:
            preset = AUDIO_FILTER_PRESETS.get(filter_id)
            if preset and preset.target == "audio":
                chain.append(preset.expression)
        return chain
