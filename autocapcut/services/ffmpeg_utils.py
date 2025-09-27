"""Utility helpers for working with FFmpeg/ffprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from loguru import logger

FFPROBE_DEFAULT = "ffprobe"


class FFprobeError(RuntimeError):
    """Raised when ffprobe cannot retrieve media information."""


def probe_duration_microseconds(path: Path, *, ffprobe: str | None = None) -> int:
    """Return duration (in microseconds) for the given media file.

    Parameters
    ----------
    path:
        File to probe.
    ffprobe:
        Optional override for ffprobe executable name/path.
    """

    if not path.exists():
        raise FFprobeError(f"Media file not found: {path}")

    executable = ffprobe or FFPROBE_DEFAULT
    if shutil.which(executable) is None:
        raise FFprobeError("ffprobe executable not available on PATH")

    cmd = [
        executable,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    logger.debug("Running ffprobe: %s", " ".join(cmd))
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - external tool
        raise FFprobeError(f"ffprobe failed for {path}: {exc.stderr}") from exc

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - unexpected
        raise FFprobeError(f"Unable to parse ffprobe output for {path}") from exc

    duration_str = (payload.get("format") or {}).get("duration")
    if duration_str is None:
        raise FFprobeError(f"ffprobe did not report duration for {path}")

    try:
        seconds = float(duration_str)
    except (TypeError, ValueError) as exc:
        raise FFprobeError(f"Invalid duration value from ffprobe: {duration_str}") from exc

    microseconds = int(seconds * 1_000_000)
    if microseconds <= 0:
        raise FFprobeError(f"ffprobe reported non-positive duration for {path}")
    return microseconds


def probe_first_existing(paths: Iterable[Path], *, ffprobe: str | None = None) -> int | None:
    """Return duration of the first existing media file from *paths* (in microseconds)."""

    for candidate in paths:
        try:
            return probe_duration_microseconds(candidate, ffprobe=ffprobe)
        except FFprobeError as exc:
            logger.debug("Skipping %s: %s", candidate, exc)
    return None
