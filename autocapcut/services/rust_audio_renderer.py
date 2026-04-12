"""Optional Rust backend bridge for Audio Visualizer spectrum rendering."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from autocapcut.services.audio_visualizer_spectrum import SpectrumLayout

ProgressCallback = Callable[[int], None]
ProcCallback = Callable[[subprocess.Popen[bytes]], None]


def _default_renderer_bin() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "rust" / "audio_spectrum_renderer" / "target" / "release" / "audio_spectrum_renderer"


def find_rust_audio_renderer_binary() -> Path | None:
    custom = os.getenv("AUTOCAPCUT_AUDIO_VIS_ENGINE_BIN", "").strip()
    if custom:
        candidate = Path(custom)
        if candidate.exists() and candidate.is_file():
            return candidate
        logger.warning("AUTOCAPCUT_AUDIO_VIS_ENGINE_BIN is set but binary does not exist: {}", custom)
        return None

    candidate = _default_renderer_bin()
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def resolve_audio_visualizer_engine() -> str:
    mode = (os.getenv("AUTOCAPCUT_AUDIO_VIS_ENGINE", "auto") or "auto").strip().lower()
    if mode not in {"auto", "python", "rust"}:
        return "auto"
    return mode


def render_spectrum_final_video_rust(
    audio_path: str,
    ffmpeg_cmd: list[str],
    layout: SpectrumLayout,
    color_hex: str,
    fps: int,
    progress_cb: Optional[ProgressCallback] = None,
    proc_cb: Optional[ProcCallback] = None,
) -> None:
    renderer_bin = find_rust_audio_renderer_binary()
    if renderer_bin is None:
        raise RuntimeError("Rust audio spectrum renderer binary not found")

    payload = {
        "audio_path": audio_path,
        "fps": fps,
        "color_hex": color_hex,
        "overlay_width": layout.overlay_width,
        "overlay_height": layout.overlay_height,
        "bins": layout.bins,
        "bar_width": layout.bar_width,
        "pitch": layout.pitch,
        "min_height": layout.min_height,
        "max_height": layout.max_height,
        "top_base_y": layout.top_base_y,
        "bottom_base_y": layout.bottom_base_y,
    }

    renderer = subprocess.Popen(
        [str(renderer_bin)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert renderer.stdin is not None
    assert renderer.stdout is not None
    assert renderer.stderr is not None

    renderer.stdin.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    renderer.stdin.close()

    ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=renderer.stdout, stderr=subprocess.PIPE)
    if proc_cb is not None:
        proc_cb(ffmpeg)
    renderer.stdout.close()

    progress_error: list[str] = []

    def _watch_progress() -> None:
        for raw_line in renderer.stderr:
            line = raw_line.decode(errors="ignore").strip()
            if line.startswith("progress="):
                if progress_cb is not None:
                    try:
                        progress_cb(int(line.split("=", 1)[1]))
                    except ValueError:
                        continue
            elif line:
                progress_error.append(line)

    progress_thread = threading.Thread(target=_watch_progress, daemon=True)
    progress_thread.start()

    ffmpeg_stderr = b""
    if ffmpeg.stderr is not None:
        ffmpeg_stderr = ffmpeg.stderr.read()
    ffmpeg_rc = ffmpeg.wait()

    if ffmpeg_rc != 0 and renderer.poll() is None:
        renderer.terminate()

    renderer_rc = renderer.wait()
    progress_thread.join(timeout=1)

    if renderer_rc != 0:
        raise RuntimeError(
            f"Rust spectrum renderer failed ({renderer_rc}): {' | '.join(progress_error[-10:])}"
        )
    if ffmpeg_rc != 0:
        raise RuntimeError(
            f"Final spectrum render failed ({ffmpeg_rc}): {ffmpeg_stderr[-500:].decode(errors='ignore')}"
        )
