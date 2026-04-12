"""Audio Visualizer service — FFmpeg-based render engine for podcast video generation."""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from loguru import logger

from autocapcut.services.audio_visualizer_spectrum import (
    SpectrumLayout,
    build_spectrum_layout,
    render_spectrum_final_video,
)
from autocapcut.services.rust_audio_renderer import (
    find_rust_audio_renderer_binary,
    render_spectrum_final_video_rust,
    resolve_audio_visualizer_engine,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

VisStyle = Literal[
    "wave", "mirror_wave", "spectrum_bars", "point_wave",
    "cqt", "vectorscope", "histogram", "cqt_color",
]

BgMode = Literal["solid", "gradient", "image"]
CoverPos = Literal["center", "bottom_third", "top_right"]
OutputFmt = Literal["mp4", "mov", "webm"]
Resolution = Literal["1920x1080", "1080x1920", "1080x1080", "custom"]


@dataclass
class VisualizerConfig:
    style: VisStyle = "wave"
    visualizer_color: str = "#00BFFF"
    background_mode: BgMode = "solid"
    background_solid_color: str = "#0a0a0a"
    background_gradient_color1: str = "#0a0a0a"
    background_gradient_color2: str = "#1a1a2e"
    background_gradient_angle: int = 135
    background_image_path: Optional[str] = None
    cover_art_path: Optional[str] = None
    cover_art_position: CoverPos = "center"
    text_title: str = ""
    text_subtitle: str = ""
    text_color: str = "#ffffff"
    text_title_size: int = 48
    text_subtitle_size: int = 28
    resolution: Resolution = "1920x1080"
    resolution_custom_w: Optional[int] = None
    resolution_custom_h: Optional[int] = None
    output_format: OutputFmt = "mp4"

    @classmethod
    def from_dict(cls, d: dict) -> "VisualizerConfig":
        valid = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in valid})

    def get_wh(self) -> tuple[int, int]:
        if self.resolution == "custom":
            w = self.resolution_custom_w or 1920
            h = self.resolution_custom_h or 1080
        else:
            parts = self.resolution.split("x")
            w, h = int(parts[0]), int(parts[1])
        # libx264 requires even dims
        return (w if w % 2 == 0 else w - 1), (h if h % 2 == 0 else h - 1)


# ---------------------------------------------------------------------------
# Job state (in-memory, v1 — not persisted across restarts)
# ---------------------------------------------------------------------------

@dataclass
class JobStatus:
    job_id: str
    status: Literal["queued", "running", "done", "failed", "cancelled", "lost"] = "queued"
    progress_pct: int = 0
    output_path: Optional[str] = None
    error: Optional[str] = None
    proc: Optional[object] = field(default=None, compare=False, repr=False)
    temp_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "output_path": self.output_path,
            "error": self.error,
        }


_jobs: dict[str, JobStatus] = {}

ASSETS_DIR = Path.home() / ".autocapcut" / "visualizer_assets"
TEMP_DIR = Path.home() / ".autocapcut" / "temp"
FONT_PATH = Path(__file__).parent.parent.parent / "resources" / "fonts" / "SpaceGrotesk.ttf"
DEFAULT_RENDER_FPS = 30
PREVIEW_SPECTRUM_CANVAS_W = 640
PREVIEW_SPECTRUM_CANVAS_H = 360


@lru_cache(maxsize=1)
def _ffmpeg_encoders() -> set[str]:
    import subprocess

    try:
        enc_out = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        return set()

    encoders: set[str] = set()
    for line in enc_out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    return encoders


def _has_encoder(name: str) -> bool:
    return name in _ffmpeg_encoders()


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

def check_dependencies() -> dict[str, bool | str]:
    """Check FFmpeg version, encoder availability, and Pillow. Returns status dict."""
    results: dict[str, bool | str] = {}

    # FFmpeg version
    try:
        import subprocess
        out = subprocess.check_output(["ffmpeg", "-version"], stderr=subprocess.STDOUT, text=True)
        m = re.search(r"ffmpeg version (\d+)\.(\d+)", out)
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            ver_ok = (major, minor) >= (4, 4)
            results["ffmpeg_version"] = f"{major}.{minor}"
            results["ffmpeg_ok"] = ver_ok
            if not ver_ok:
                logger.warning("FFmpeg {}.{} found — minimum required is 4.4", major, minor)
        else:
            results["ffmpeg_ok"] = False
            logger.warning("Could not parse FFmpeg version")
    except (FileNotFoundError, Exception) as e:
        results["ffmpeg_ok"] = False
        logger.error("FFmpeg not found: {}", e)

    # Encoders
    try:
        encoders = _ffmpeg_encoders()
        results["libopus"] = "libopus" in encoders
        results["libvpx_vp9"] = "libvpx-vp9" in encoders
        results["h264_videotoolbox"] = "h264_videotoolbox" in encoders
        results["hevc_videotoolbox"] = "hevc_videotoolbox" in encoders
        if not results["libopus"] or not results["libvpx_vp9"]:
            logger.warning("WebM encoders (libopus/libvpx-vp9) not available — WebM output disabled")
    except Exception:
        results["libopus"] = False
        results["libvpx_vp9"] = False
        results["h264_videotoolbox"] = False
        results["hevc_videotoolbox"] = False

    # Pillow
    try:
        from PIL import Image  # noqa: F401
        results["pillow"] = True
    except ImportError:
        results["pillow"] = False
        logger.warning("Pillow not importable — gradient backgrounds disabled")

    return results


# ---------------------------------------------------------------------------
# Background generation
# ---------------------------------------------------------------------------

def _generate_gradient_png(color1: str, color2: str, angle: int, w: int, h: int) -> Path:
    """Generate a gradient PNG using Pillow. Returns temp file path."""
    from PIL import Image  # type: ignore[import]
    import math

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TEMP_DIR / f"gradient_{uuid.uuid4().hex}.png"

    img = Image.new("RGB", (w, h))
    pixels = img.load()

    rad = math.radians(angle)
    dx, dy = math.cos(rad), math.sin(rad)

    def hex_to_rgb(hx: str) -> tuple[int, int, int]:
        hx = hx.lstrip("#")
        return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)

    r1, g1, b1 = hex_to_rgb(color1)
    r2, g2, b2 = hex_to_rgb(color2)

    cx, cy = w / 2, h / 2
    max_dist = abs(dx * cx) + abs(dy * cy) or 1

    for y in range(h):
        for x in range(w):
            t = ((x - cx) * dx + (y - cy) * dy) / max_dist
            t = max(0.0, min(1.0, (t + 1) / 2))
            pixels[x, y] = (  # type: ignore[index]
                int(r1 + (r2 - r1) * t),
                int(g1 + (g2 - g1) * t),
                int(b1 + (b2 - b1) * t),
            )

    img.save(str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# FFmpeg command builder
# ---------------------------------------------------------------------------

def _hex_to_ffmpeg_color(hx: str) -> str:
    """Convert #RRGGBB → 0xRRGGBB for FFmpeg."""
    return "0x" + hx.lstrip("#").upper()


def _escape_drawtext(text: str) -> str:
    """Escape special chars for FFmpeg drawtext."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _video_codec_args(config: VisualizerConfig, width: int, height: int) -> list[str]:
    fmt = config.output_format
    pixels = width * height
    is_full_hd_plus = pixels >= 1920 * 1080

    if fmt == "webm":
        return [
            "-c:v", "libvpx-vp9",
            "-deadline", "realtime",
            "-cpu-used", "4",
            "-row-mt", "1",
            "-b:v", "0",
            "-crf", "33",
            "-c:a", "libopus",
        ]

    if _has_encoder("h264_videotoolbox"):
        bitrate = "10M" if is_full_hd_plus else "6M"
        maxrate = "14M" if is_full_hd_plus else "8M"
        bufsize = "20M" if is_full_hd_plus else "12M"
        return [
            "-c:v", "h264_videotoolbox",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-realtime", "1",
            "-prio_speed", "1",
            "-allow_sw", "1",
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-g", str(DEFAULT_RENDER_FPS * 2),
            "-c:a", "aac",
            "-b:a", "192k",
        ]

    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21" if is_full_hd_plus else "22",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-tune", "animation",
        "-g", str(DEFAULT_RENDER_FPS * 2),
        "-c:a", "aac",
        "-b:a", "192k",
    ]


def _even(value: int) -> int:
    return value if value % 2 == 0 else value - 1


def _visualizer_sizes(style: VisStyle, width: int, height: int) -> tuple[int, int, int]:
    if style in {"wave", "mirror_wave", "point_wave"}:
        out_h = _even(max(120, height // 4))
        return _even(min(width, 960)), _even(min(out_h, 180)), out_h
    if style == "spectrum_bars":
        out_h = _even(max(180, height // 3))
        return _even(min(width, 960)), _even(min(out_h, 240)), out_h
    if style in {"histogram", "cqt", "cqt_color"}:
        out_h = _even(max(180, height // 3))
        return _even(min(width, 1024)), _even(min(out_h, 256)), out_h
    out_h = _even(max(180, height // 3))
    return _even(min(width, 720)), _even(min(out_h, 240)), out_h


def build_ffmpeg_command(
    audio_path: str,
    output_path: str,
    config: VisualizerConfig,
    duration_s: float,
    bg_image_path: Optional[str] = None,   # gradient PNG or solid BG PNG path
    font_path: Optional[str] = None,
    cover_path: Optional[str] = None,
) -> list[str]:
    """Build the full FFmpeg filter_complex command."""
    w, h = config.get_wh()
    vis_color = _hex_to_ffmpeg_color(config.visualizer_color)
    frame_rate = DEFAULT_RENDER_FPS
    vis_w, vis_h, vis_out_h = _visualizer_sizes(config.style, w, h)
    spectrum_display_w = _even(max(640, min(w - 160, int(w * 0.78))))
    spectrum_gap = _even(max(64, int(h * 0.085)))
    spectrum_band_h = _even(max(140, min(int(h * 0.19), 220)))
    spectrum_x = max(0, (w - spectrum_display_w) // 2)
    spectrum_top_y = max(0, (h // 2) - (spectrum_gap // 2) - spectrum_band_h)
    spectrum_bottom_y = min(h - spectrum_band_h, (h // 2) + (spectrum_gap // 2))
    spectrum_pitch = max(10, spectrum_display_w // 96)
    spectrum_gap_w = max(2, spectrum_pitch // 4)

    # ── Inputs ──────────────────────────────────────────────────────────────
    inputs: list[str] = ["-i", audio_path]
    input_idx = 1  # 0 = audio

    # Background input
    if bg_image_path:
        inputs += ["-i", bg_image_path]
        bg_input = f"[{input_idx}:v]"
        input_idx += 1
    else:
        # Solid color via lavfi
        solid = _hex_to_ffmpeg_color(config.background_solid_color)
        inputs += [
            "-f", "lavfi",
            "-i", f"color=c={solid}:s={w}x{h}:d={duration_s}",
        ]
        bg_input = f"[{input_idx}:v]"
        input_idx += 1

    # Cover art input
    cover_input_idx = None
    if cover_path and Path(cover_path).exists():
        inputs += ["-i", cover_path]
        cover_input_idx = input_idx
        input_idx += 1

    # ── Filter graph ─────────────────────────────────────────────────────────
    fc_parts: list[str] = []
    last_v = "bg_scaled"

    # Scale background to target resolution
    if bg_image_path:
        fc_parts.append(f"{bg_input}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[bg_scaled]")
    else:
        fc_parts.append(f"{bg_input}scale={w}:{h}[bg_scaled]")

    # Visualizer filter
    vis_label = "vis_out"
    style = config.style

    if style == "wave":
        fc_parts.append(
            f"[0:a]showwaves=s={vis_w}x{vis_h}:mode=line:colors={vis_color}:scale=sqrt:draw=scale:r={frame_rate}[vis_small];"
            f"[vis_small]scale={w}:{vis_out_h}:flags=fast_bilinear[vis_raw];"
            f"[vis_raw]pad={w}:{h}:0:(oh-ih)/2:color=black@0[{vis_label}]"
        )
    elif style == "mirror_wave":
        fc_parts.append(
            f"[0:a]showwaves=s={vis_w}x{vis_h}:mode=cline:colors={vis_color}:scale=sqrt:draw=scale:r={frame_rate}[vis_small];"
            f"[vis_small]scale={w}:{vis_out_h}:flags=fast_bilinear[vis_raw];"
            f"[vis_raw]pad={w}:{h}:0:(oh-ih)/2:color=black@0[{vis_label}]"
        )
    elif style == "spectrum_bars":
        fc_parts.append(
            f"[0:a]showfreqs=s={vis_w}x{vis_h}:mode=bar:r={frame_rate}:ascale=log:fscale=log:win_size=1024:overlap=0.75:averaging=2:colors={vis_color}[vis_small];"
            f"[vis_small]scale={spectrum_display_w}:{spectrum_band_h}:flags=neighbor,"
            f"drawgrid=w={spectrum_pitch}:h={spectrum_band_h + 200}:t={spectrum_gap_w}:c=black@1,"
            f"format=rgba,colorkey=0x000000:0.08:0.0[vis_core];"
            f"[vis_core]split=4[vis_top][vis_bottom_src][vis_top_glow_src][vis_bottom_glow_src];"
            f"[vis_bottom_src]vflip[vis_bottom];"
            f"[vis_top_glow_src]gblur=sigma=10,colorchannelmixer=aa=0.34[vis_top_glow];"
            f"[vis_bottom_glow_src]vflip,gblur=sigma=10,colorchannelmixer=aa=0.34[vis_bottom_glow];"
            f"[bg_scaled][vis_top_glow]overlay={spectrum_x}:{spectrum_top_y}[spec_bg_1];"
            f"[spec_bg_1][vis_bottom_glow]overlay={spectrum_x}:{spectrum_bottom_y}[spec_bg_2];"
            f"[spec_bg_2][vis_top]overlay={spectrum_x}:{spectrum_top_y}[spec_bg_3];"
            f"[spec_bg_3][vis_bottom]overlay={spectrum_x}:{spectrum_bottom_y}[spec_bg_4];"
            f"[spec_bg_4]drawbox=x={spectrum_x}:y={spectrum_top_y + spectrum_band_h - 2}:w={spectrum_display_w}:h=2:color={vis_color}@0.30:t=fill[spec_bg_5];"
            f"[spec_bg_5]drawbox=x={spectrum_x}:y={spectrum_bottom_y}:w={spectrum_display_w}:h=2:color={vis_color}@0.30:t=fill[{vis_label}]"
        )
    elif style == "point_wave":
        fc_parts.append(
            f"[0:a]showwaves=s={vis_w}x{vis_h}:mode=p2p:colors={vis_color}:scale=sqrt:draw=scale:r={frame_rate}[vis_small];"
            f"[vis_small]scale={w}:{vis_out_h}:flags=fast_bilinear[vis_raw];"
            f"[vis_raw]pad={w}:{h}:0:(oh-ih)/2:color=black@0[{vis_label}]"
        )
    elif style == "cqt":
        fc_parts.append(
            f"[0:a]showcqt=s={vis_w}x{vis_h}:axis=0:sono_v=full[vis_small];"
            f"[vis_small]scale={w}:{vis_out_h}:flags=fast_bilinear[vis_raw];"
            f"[vis_raw]pad={w}:{h}:0:(oh-ih)/2:color=black@0[{vis_label}]"
        )
    elif style == "vectorscope":
        # Handle mono: duplicate channel to stereo for useful display
        fc_parts.append(
            f"[0:a]pan=stereo|c0=c0|c1=c0,avectorscope=s={h//2}x{h//2}:zoom=1.5[vis_raw];"
            f"[vis_raw]pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0[{vis_label}]"
        )
    elif style == "histogram":
        fc_parts.append(
            f"[0:a]ahistogram=s={vis_w}x{vis_h}:dmode=magnitude[vis_small];"
            f"[vis_small]scale={w}:{vis_out_h}:flags=fast_bilinear[vis_raw];"
            f"[vis_raw]pad={w}:{h}:0:(oh-ih)/2:color=black@0[{vis_label}]"
        )
    elif style == "cqt_color":
        c1 = vis_color
        fc_parts.append(
            f"[0:a]showcqt=s={vis_w}x{vis_h}:axis=0:cscheme={c1}|0x101080:sono_v=full[vis_small];"
            f"[vis_small]scale={w}:{vis_out_h}:flags=fast_bilinear[vis_raw];"
            f"[vis_raw]pad={w}:{h}:0:(oh-ih)/2:color=black@0[{vis_label}]"
        )
    else:
        # Fallback to wave
        fc_parts.append(
            f"[0:a]showwaves=s={vis_w}x{vis_h}:mode=line:colors={vis_color}:draw=scale:r={frame_rate}[vis_small];"
            f"[vis_small]scale={w}:{vis_out_h}:flags=fast_bilinear[vis_raw];"
            f"[vis_raw]pad={w}:{h}:0:(oh-ih)/2:color=black@0[{vis_label}]"
        )

    # Overlay visualizer on background unless the style already composited onto bg.
    if style == "spectrum_bars":
        last_v = vis_label
    else:
        merged_label = "merged"
        fc_parts.append(
            f"[{last_v}][{vis_label}]overlay=0:0:format=auto[{merged_label}]"
        )
        last_v = merged_label

    # Cover art overlay
    if cover_input_idx is not None:
        cw, ch = 400, 400  # max display size (Python resizes source if >600px)
        if config.cover_art_position == "center":
            cx = f"(W-{cw})/2"
            cy = f"(H-{ch})/2"
        elif config.cover_art_position == "bottom_third":
            cx = f"(W-{cw})/2"
            cy = f"H*2/3-{ch//2}"
        else:  # top_right
            cx, cy = f"W-{cw}-40", "40"

        cover_scaled = f"cover_scaled"
        fc_parts.append(
            f"[{cover_input_idx}:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease[{cover_scaled}]"
        )
        cover_out = "cover_out"
        fc_parts.append(
            f"[{last_v}][{cover_scaled}]overlay={cx}:{cy}[{cover_out}]"
        )
        last_v = cover_out

    # Text overlays
    fp = str(font_path) if font_path and Path(str(font_path)).exists() else "Arial"
    text_v = last_v

    if config.text_title.strip():
        safe_title = _escape_drawtext(config.text_title[:80])
        tc = _hex_to_ffmpeg_color(config.text_color)
        title_out = "title_out"
        fc_parts.append(
            f"[{text_v}]drawtext=fontfile='{fp}':text='{safe_title}':"
            f"fontcolor={tc}:fontsize={config.text_title_size}:"
            f"x=(W-tw)/2:y=H*0.82:"
            f"box=1:boxcolor=black@0.4:boxborderw=10[{title_out}]"
        )
        text_v = title_out

    if config.text_subtitle.strip():
        safe_sub = _escape_drawtext(config.text_subtitle[:100])
        tc = _hex_to_ffmpeg_color(config.text_color)
        sub_out = "sub_out"
        fc_parts.append(
            f"[{text_v}]drawtext=fontfile='{fp}':text='{safe_sub}':"
            f"fontcolor={tc}:fontsize={config.text_subtitle_size}:"
            f"x=(W-tw)/2:y=H*0.90:"
            f"box=1:boxcolor=black@0.4:boxborderw=8[{sub_out}]"
        )
        text_v = sub_out

    last_v = text_v

    # ── Codec + format ────────────────────────────────────────────────────────
    codec_args = _video_codec_args(config, w, h)

    filter_complex = ";".join(fc_parts)

    cmd = [
        "ffmpeg", "-y",
        "-progress", "pipe:1", "-nostats",
        "-threads", "0",
        "-sws_flags", "fast_bilinear",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{last_v}]",
        "-map", "0:a",
        "-r", str(frame_rate),
        "-shortest",
        *codec_args,
        "-movflags", "+faststart",
        output_path,
    ]
    return cmd


def _append_cover_and_text_filters(
    fc_parts: list[str],
    last_v: str,
    cover_input_idx: Optional[int],
    cover_path: Optional[str],
    config: VisualizerConfig,
    font_path: Optional[str],
) -> str:
    if cover_input_idx is not None and cover_path and Path(cover_path).exists():
        cw, ch = 400, 400
        if config.cover_art_position == "center":
            cx = f"(W-{cw})/2"
            cy = f"(H-{ch})/2"
        elif config.cover_art_position == "bottom_third":
            cx = f"(W-{cw})/2"
            cy = f"H*2/3-{ch//2}"
        else:
            cx, cy = f"W-{cw}-40", "40"

        cover_scaled = "cover_scaled"
        fc_parts.append(
            f"[{cover_input_idx}:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease[{cover_scaled}]"
        )
        cover_out = "cover_out"
        fc_parts.append(f"[{last_v}][{cover_scaled}]overlay={cx}:{cy}[{cover_out}]")
        last_v = cover_out

    fp = str(font_path) if font_path and Path(str(font_path)).exists() else "Arial"
    if config.text_title.strip():
        safe_title = _escape_drawtext(config.text_title[:80])
        tc = _hex_to_ffmpeg_color(config.text_color)
        title_out = "title_out"
        fc_parts.append(
            f"[{last_v}]drawtext=fontfile='{fp}':text='{safe_title}':"
            f"fontcolor={tc}:fontsize={config.text_title_size}:"
            f"x=(W-tw)/2:y=H*0.82:"
            f"box=1:boxcolor=black@0.4:boxborderw=10[{title_out}]"
        )
        last_v = title_out

    if config.text_subtitle.strip():
        safe_sub = _escape_drawtext(config.text_subtitle[:100])
        tc = _hex_to_ffmpeg_color(config.text_color)
        sub_out = "sub_out"
        fc_parts.append(
            f"[{last_v}]drawtext=fontfile='{fp}':text='{safe_sub}':"
            f"fontcolor={tc}:fontsize={config.text_subtitle_size}:"
            f"x=(W-tw)/2:y=H*0.90:"
            f"box=1:boxcolor=black@0.4:boxborderw=8[{sub_out}]"
        )
        last_v = sub_out

    return last_v


def build_spectrum_composite_command(
    audio_path: str,
    overlay_path: str,
    layout: SpectrumLayout,
    output_path: str,
    config: VisualizerConfig,
    duration_s: float,
    bg_image_path: Optional[str] = None,
    font_path: Optional[str] = None,
    cover_path: Optional[str] = None,
    source_layout: Optional[SpectrumLayout] = None,
) -> list[str]:
    w, h = config.get_wh()

    inputs: list[str] = ["-i", audio_path]
    input_idx = 1

    if bg_image_path:
        inputs += ["-i", bg_image_path]
        bg_input = f"[{input_idx}:v]"
        input_idx += 1
    else:
        solid = _hex_to_ffmpeg_color(config.background_solid_color)
        inputs += ["-f", "lavfi", "-i", f"color=c={solid}:s={w}x{h}:d={duration_s}"]
        bg_input = f"[{input_idx}:v]"
        input_idx += 1

    cover_input_idx = None
    if cover_path and Path(cover_path).exists():
        inputs += ["-i", cover_path]
        cover_input_idx = input_idx
        input_idx += 1

    inputs += ["-i", overlay_path]
    overlay_input = f"[{input_idx}:v]"

    fc_parts: list[str] = []
    if bg_image_path:
        fc_parts.append(f"{bg_input}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[bg_scaled]")
    else:
        fc_parts.append(f"{bg_input}scale={w}:{h}[bg_scaled]")

    if source_layout and (
        source_layout.overlay_width != layout.overlay_width
        or source_layout.overlay_height != layout.overlay_height
    ):
        fc_parts.append(
            f"{overlay_input}scale={layout.overlay_width}:{layout.overlay_height}:flags=lanczos[spec_scaled]"
        )
        overlay_input = "[spec_scaled]"

    fc_parts.append(f"{overlay_input}format=rgba,colorkey=0x000000:0.08:0.0[spec_rgba]")
    fc_parts.append("[spec_rgba]split=2[spec_core][spec_glow_src]")
    fc_parts.append("[spec_glow_src]gblur=sigma=10,colorchannelmixer=aa=0.34[spec_glow]")
    fc_parts.append(f"[bg_scaled][spec_glow]overlay={layout.overlay_x}:{layout.overlay_y}[spec_merged_1]")
    fc_parts.append(f"[spec_merged_1][spec_core]overlay={layout.overlay_x}:{layout.overlay_y}[spec_merged_2]")

    last_v = _append_cover_and_text_filters(
        fc_parts=fc_parts,
        last_v="spec_merged_2",
        cover_input_idx=cover_input_idx,
        cover_path=cover_path,
        config=config,
        font_path=font_path,
    )

    cmd = [
        "ffmpeg", "-y",
        "-progress", "pipe:1", "-nostats",
        "-threads", "0",
        "-sws_flags", "fast_bilinear",
        *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", f"[{last_v}]",
        "-map", "0:a",
        "-r", str(DEFAULT_RENDER_FPS),
        "-shortest",
        *_video_codec_args(config, w, h),
        "-movflags", "+faststart",
        output_path,
    ]
    return cmd


def build_spectrum_pipe_command(
    audio_path: str,
    source_layout: SpectrumLayout,
    display_layout: SpectrumLayout,
    output_path: str,
    config: VisualizerConfig,
    duration_s: float,
    bg_image_path: Optional[str] = None,
    font_path: Optional[str] = None,
    cover_path: Optional[str] = None,
) -> list[str]:
    w, h = config.get_wh()

    inputs: list[str] = ["-i", audio_path]
    input_idx = 1

    if bg_image_path:
        inputs += ["-i", bg_image_path]
        bg_input = f"[{input_idx}:v]"
        input_idx += 1
    else:
        solid = _hex_to_ffmpeg_color(config.background_solid_color)
        inputs += ["-f", "lavfi", "-i", f"color=c={solid}:s={w}x{h}:d={duration_s}"]
        bg_input = f"[{input_idx}:v]"
        input_idx += 1

    cover_input_idx = None
    if cover_path and Path(cover_path).exists():
        inputs += ["-i", cover_path]
        cover_input_idx = input_idx
        input_idx += 1

    inputs += [
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{source_layout.overlay_width}x{source_layout.overlay_height}",
        "-r", str(DEFAULT_RENDER_FPS),
        "-i", "pipe:0",
    ]
    overlay_input = f"[{input_idx}:v]"

    fc_parts: list[str] = []
    if bg_image_path:
        fc_parts.append(f"{bg_input}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[bg_scaled]")
    else:
        fc_parts.append(f"{bg_input}scale={w}:{h}[bg_scaled]")

    if (
        source_layout.overlay_width != display_layout.overlay_width
        or source_layout.overlay_height != display_layout.overlay_height
    ):
        fc_parts.append(
            f"{overlay_input}scale={display_layout.overlay_width}:{display_layout.overlay_height}:flags=lanczos[spec_scaled]"
        )
        overlay_input = "[spec_scaled]"

    fc_parts.append(f"{overlay_input}format=rgba,colorkey=0x000000:0.08:0.0[spec_rgba]")
    fc_parts.append("[spec_rgba]split=2[spec_core][spec_glow_src]")
    fc_parts.append("[spec_glow_src]gblur=sigma=10,colorchannelmixer=aa=0.34[spec_glow]")
    fc_parts.append(f"[bg_scaled][spec_glow]overlay={display_layout.overlay_x}:{display_layout.overlay_y}[spec_merged_1]")
    fc_parts.append(f"[spec_merged_1][spec_core]overlay={display_layout.overlay_x}:{display_layout.overlay_y}[spec_merged_2]")

    last_v = _append_cover_and_text_filters(
        fc_parts=fc_parts,
        last_v="spec_merged_2",
        cover_input_idx=cover_input_idx,
        cover_path=cover_path,
        config=config,
        font_path=font_path,
    )

    return [
        "ffmpeg", "-y",
        "-v", "error", "-nostats",
        "-threads", "0",
        "-sws_flags", "fast_bilinear",
        *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", f"[{last_v}]",
        "-map", "0:a",
        "-r", str(DEFAULT_RENDER_FPS),
        "-shortest",
        *_video_codec_args(config, w, h),
        "-movflags", "+faststart",
        output_path,
    ]


# ---------------------------------------------------------------------------
# ffprobe duration
# ---------------------------------------------------------------------------

async def get_audio_duration(audio_path: str) -> float:
    """Return duration in seconds via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Render entry point
# ---------------------------------------------------------------------------

async def render_visualizer(
    job_id: str,
    audio_path: str,
    config: VisualizerConfig,
) -> None:
    """Async render task. Updates _jobs[job_id] in place."""
    job = _jobs.get(job_id)
    if not job:
        return

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    audio_p = Path(audio_path)

    # Determine output path
    out_dir = audio_p.parent if _is_writable(audio_p.parent) else Path.home() / "Downloads"
    stem = audio_p.stem
    output_path = str(out_dir / f"{stem}_visualizer.{config.output_format}")

    job.status = "running"
    job.output_path = output_path
    logger.info("Render started: job_id={} style={} audio={}", job_id, config.style, audio_path)

    temp_bg: Optional[Path] = None

    try:
        # Duration
        duration_s = await get_audio_duration(audio_path)
        if duration_s <= 0:
            raise ValueError("Could not determine audio duration — file may be corrupt or unsupported")

        w, h = config.get_wh()

        # Background
        bg_image_path: Optional[str] = None
        if config.background_mode == "gradient":
            temp_bg = _generate_gradient_png(
                config.background_gradient_color1,
                config.background_gradient_color2,
                config.background_gradient_angle,
                w, h,
            )
            bg_image_path = str(temp_bg)
            job.temp_files.append(bg_image_path)
        elif config.background_mode == "image" and config.background_image_path:
            if Path(config.background_image_path).exists():
                bg_image_path = config.background_image_path

        # Cover art
        cover_path: Optional[str] = None
        if config.cover_art_path and Path(config.cover_art_path).exists():
            cover_path = config.cover_art_path

        # Font
        font_path = str(FONT_PATH) if FONT_PATH.exists() else None

        if config.style == "spectrum_bars":
            display_layout = build_spectrum_layout(w, h)
            source_layout = build_spectrum_layout(PREVIEW_SPECTRUM_CANVAS_W, PREVIEW_SPECTRUM_CANVAS_H)
            engine_mode = resolve_audio_visualizer_engine()
            use_rust_renderer = engine_mode == "rust" and find_rust_audio_renderer_binary() is not None

            def _progress_overlay(value: int) -> None:
                job.progress_pct = max(job.progress_pct, min(95, value))

            cmd = build_spectrum_pipe_command(
                audio_path=audio_path,
                source_layout=source_layout,
                display_layout=display_layout,
                output_path=output_path,
                config=config,
                duration_s=duration_s,
                bg_image_path=bg_image_path,
                font_path=font_path,
                cover_path=cover_path,
            )

            def _attach_proc(proc: object) -> None:
                job.proc = proc

            renderer_impl = render_spectrum_final_video_rust if use_rust_renderer else render_spectrum_final_video
            renderer_args: tuple[object, ...]
            if use_rust_renderer:
                logger.info("Using Rust spectrum renderer: job_id={} mode={}", job_id, engine_mode)
                renderer_args = (
                    audio_path,
                    cmd,
                    source_layout,
                    config.visualizer_color,
                    DEFAULT_RENDER_FPS,
                    _progress_overlay,
                    _attach_proc,
                )
            else:
                renderer_args = (
                    audio_path,
                    cmd,
                    source_layout.overlay_width,
                    source_layout.overlay_height,
                    config.visualizer_color,
                    DEFAULT_RENDER_FPS,
                    _progress_overlay,
                    _attach_proc,
                )

            await asyncio.to_thread(renderer_impl, *renderer_args)
            job.proc = None
            if job.status != "cancelled":
                job.status = "done"
                job.progress_pct = 100
                logger.info("Render done: job_id={} output={}", job_id, output_path)
            return
        else:
            cmd = build_ffmpeg_command(
                audio_path=audio_path,
                output_path=output_path,
                config=config,
                duration_s=duration_s,
                bg_image_path=bg_image_path,
                font_path=font_path,
                cover_path=cover_path,
            )
        logger.debug("FFmpeg cmd: {}", " ".join(cmd))

        # Spawn
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        job.proc = proc

        # Read progress from stdout (-progress pipe:1)
        duration_ms = duration_s * 1_000_000  # microseconds

        async def _read_progress() -> None:
            assert proc.stdout
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="ignore").strip()
                if line.startswith("out_time_ms="):
                    try:
                        out_us = float(line.split("=")[1])
                        if duration_ms > 0:
                            ratio = max(0.0, min(1.0, out_us / duration_ms))
                            if config.style == "spectrum_bars":
                                composite_progress = 70 + int(ratio * 29)
                                job.progress_pct = max(job.progress_pct, min(99, composite_progress))
                            else:
                                job.progress_pct = min(99, int(ratio * 100))
                    except ValueError:
                        pass

        await asyncio.gather(_read_progress(), proc.wait())

        if proc.returncode == 0:
            job.status = "done"
            job.progress_pct = 100
            logger.info("Render done: job_id={} output={}", job_id, output_path)
        elif job.status != "cancelled":
            stderr_data = b""
            if proc.stderr:
                stderr_data = await proc.stderr.read()
            job.status = "failed"
            job.error = f"FFmpeg exited {proc.returncode}: {stderr_data[-500:].decode(errors='ignore')}"
            logger.error("Render failed: job_id={} error={}", job_id, job.error)

    except Exception as exc:
        if job.status == "cancelled":
            logger.info("Render cancelled during exception path: job_id={}", job_id)
        else:
            job.status = "failed"
            job.error = str(exc)
            logger.error("Render exception: job_id={} error={}", job_id, exc)
    finally:
        # Clean up temp files
        for tf in job.temp_files:
            try:
                Path(tf).unlink(missing_ok=True)
            except Exception:
                pass
        # Clean up partial output on cancel/fail
        if job.status in ("cancelled", "failed"):
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass


async def cancel_render(job_id: str) -> bool:
    """Terminate the render process. Returns True if cancelled."""
    job = _jobs.get(job_id)
    if not job or job.proc is None:
        return False

    job.status = "cancelled"
    try:
        proc = job.proc
        proc.terminate()
        wait_result = proc.wait()
        if inspect.isawaitable(wait_result):
            try:
                await asyncio.wait_for(wait_result, timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        else:
            import subprocess

            try:
                await asyncio.to_thread(proc.wait, timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait)
    except ProcessLookupError:
        pass

    logger.info("Render cancelled: job_id={}", job_id)
    return True


def get_job(job_id: str) -> JobStatus:
    """Return job or a synthetic 'lost' status if not found."""
    return _jobs.get(job_id) or JobStatus(
        job_id=job_id,
        status="lost",
        error="job_not_found_after_restart",
    )


def create_job() -> str:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = JobStatus(job_id=job_id)
    return job_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_writable(path: Path) -> bool:
    try:
        import os
        return os.access(str(path), os.W_OK)
    except Exception:
        return False


def validate_hex_color(color: str) -> bool:
    return bool(re.match(r"^#[0-9a-fA-F]{6}$", color))


def validate_resolution(w: int, h: int) -> Optional[str]:
    if w % 2 != 0 or h % 2 != 0:
        return "Dimensions must be even numbers (libx264 requirement)"
    if w < 480 or h < 480:
        return "Minimum dimension is 480px"
    if w > 3840 or h > 3840:
        return "Maximum dimension is 3840px"
    ratio = max(w, h) / min(w, h)
    if ratio > 4.0:
        return "Aspect ratio must be 4:1 or less"
    return None
