"""FFmpeg-based rendering helpers for compatible CapCut timelines."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from autocapcut.models import ProjectItem


class FFmpegRenderError(RuntimeError):
    """Raised when FFmpeg rendering fails."""


def render_project_with_ffmpeg(project: ProjectItem, plan: Dict[str, Any], output_path: Path) -> None:
    """Render *project* timeline to *output_path* using FFmpeg based on *plan*."""

    if not plan.get("ready"):
        raise FFmpegRenderError("Project is not flagged as FFmpeg-ready.")

    segments = plan.get("segments") or []
    if not segments:
        raise FFmpegRenderError("No segments defined in FFmpeg plan.")

    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = plan.get("ffmpeg_bin", "ffmpeg")
    fps = int(plan.get("fps") or 30)
    canvas = plan.get("canvas") or {}
    target_width = int(canvas.get("width") or 1080)
    target_height = int(canvas.get("height") or 1920)

    temp_dir = Path(tempfile.mkdtemp(prefix="autocapcut_ffmpeg_"))
    logger.debug("Rendering via FFmpeg in temp dir %s", temp_dir)
    try:
        segment_files = _render_segments(
            project,
            segments,
            temp_dir,
            ffmpeg_bin,
            fps,
            target_width,
            target_height,
        )

        concat_video = temp_dir / "concat.mp4"
        _concat_segments(segment_files, concat_video, ffmpeg_bin)

        audio_plan = plan.get("audio") or {}
        if audio_plan.get("path"):
            _mux_audio(concat_video, audio_plan, output_path, ffmpeg_bin)
        else:
            shutil.move(concat_video, output_path)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    logger.info("Rendered project %s to %s via FFmpeg", project.name, output_path)


def _render_segments(
    project: ProjectItem,
    segments: List[Dict[str, Any]],
    temp_dir: Path,
    ffmpeg_bin: str,
    fps: int,
    width: int,
    height: int,
) -> List[Path]:
    segment_files: List[Path] = []

    for segment in segments:
        src_path = Path(segment["path"]).expanduser()
        if not src_path.exists():
            raise FFmpegRenderError(f"Source media missing: {src_path}")

        duration_us = int(segment.get("target_duration_us") or 0)
        if duration_us <= 0:
            raise FFmpegRenderError("Segment duration must be positive")
        duration_s = duration_us / 1_000_000

        fade_in_s = max(0.0, (segment.get("fade_in_us") or 0) / 1_000_000)
        fade_out_s = max(0.0, (segment.get("fade_out_us") or 0) / 1_000_000)
        fade_out_s = min(fade_out_s, duration_s)

        vf_filters = [
            f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        ]
        if fade_in_s > 0:
            vf_filters.append(f"fade=t=in:st=0:d={fade_in_s}")
        if fade_out_s > 0:
            start_out = max(0.0, duration_s - fade_out_s)
            vf_filters.append(f"fade=t=out:st={start_out}:d={fade_out_s}")
        vf_filters.append("format=yuv420p")

        output_file = temp_dir / f"segment_{segment['index']:03d}.mp4"
        segment_files.append(output_file)

        base_cmd = [ffmpeg_bin, "-y"]
        seg_type = segment.get("type")

        if seg_type == "image":
            cmd = base_cmd + [
                "-loop",
                "1",
                "-framerate",
                str(fps),
                "-i",
                str(src_path),
                "-t",
                f"{duration_s:.6f}",
                "-vf",
                ",".join(vf_filters),
                "-r",
                str(fps),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                str(output_file),
            ]
        else:
            start_us = int(segment.get("source_start_us") or 0)
            cmd = base_cmd + [
                "-ss",
                f"{start_us / 1_000_000:.6f}",
                "-i",
                str(src_path),
                "-t",
                f"{duration_s:.6f}",
                "-vf",
                ",".join(vf_filters),
                "-r",
                str(fps),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                str(output_file),
            ]

        _run_command(cmd, description=f"segment {segment['index']}")

    return segment_files


def _concat_segments(segment_files: List[Path], output_file: Path, ffmpeg_bin: str) -> None:
    concat_list = output_file.with_suffix(".txt")
    concat_list.write_text("\n".join(f"file '{path}'" for path in segment_files), encoding="utf-8")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(output_file),
    ]
    _run_command(cmd, description="concat segments")


def _mux_audio(video_file: Path, audio_plan: Dict[str, Any], output_path: Path, ffmpeg_bin: str) -> None:
    audio_path = Path(audio_plan["path"]).expanduser()
    if not audio_path.exists():
        raise FFmpegRenderError(f"Audio file missing: {audio_path}")

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_file),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output_path),
    ]
    _run_command(cmd, description="mux audio")


def _run_command(cmd: List[str], *, description: str) -> None:
    logger.debug("Running FFmpeg command (%s): %s", description, " ".join(cmd))
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegRenderError("FFmpeg executable not found. Install FFmpeg and ensure it is in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("FFmpeg failed (%s): %s", description, exc.stderr)
        raise FFmpegRenderError(f"FFmpeg failed during {description}: {exc.stderr.strip()}")
    else:
        if result.stderr:
            logger.debug(result.stderr)
