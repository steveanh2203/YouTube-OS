"""Optional Rust backend bridge for SRT matching."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, List, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from autocapcut.services.srt_generator import ContentItem, OutputEntry, SRTSegment


ProgressCallback = Callable[[int, str], None]


def _emit_progress(progress_cb: ProgressCallback | None, percent: int, message: str) -> None:
    if not progress_cb:
        return
    progress_cb(max(0, min(100, percent)), message)


def _default_engine_bin() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "rust" / "srt_engine" / "target" / "release" / "srt_engine"


def find_rust_engine_binary() -> Path | None:
    custom = os.getenv("AUTOCAPCUT_SRT_ENGINE_BIN", "").strip()
    if custom:
        candidate = Path(custom)
        if candidate.exists() and candidate.is_file():
            return candidate
        logger.warning("AUTOCAPCUT_SRT_ENGINE_BIN is set but binary does not exist: {}", custom)
        return None

    candidate = _default_engine_bin()
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _build_payload(content_items: List["ContentItem"], srt_segments: List["SRTSegment"]) -> dict:
    return {
        "content_items": [
            {"index": item.index, "text": item.text}
            for item in content_items
        ],
        "srt_segments": [
            {"index": seg.index, "start": seg.start, "end": seg.end, "text": seg.text}
            for seg in srt_segments
        ],
    }


def _parse_output_entries(raw_entries: list[dict], content_by_index: dict[int, "ContentItem"], srt_segments: List["SRTSegment"]) -> List["OutputEntry"]:
    from autocapcut.services.srt_generator import OutputEntry

    parsed: List[OutputEntry] = []
    for row in raw_entries:
        content_index = int(row["content_index"])
        start_segment = int(row["start_segment"])
        end_segment = int(row["end_segment"])

        if start_segment < 0 or end_segment < 0 or start_segment >= len(srt_segments) or end_segment >= len(srt_segments):
            raise ValueError(f"Rust engine returned invalid segment range: {start_segment}..{end_segment}")
        if end_segment < start_segment:
            raise ValueError(f"Rust engine returned reversed segment range: {start_segment}..{end_segment}")
        content_item = content_by_index.get(content_index)
        if content_item is None:
            raise ValueError(f"Rust engine returned unknown content index: {content_index}")

        parsed.append(
            OutputEntry(
                index=content_item.index,
                start=srt_segments[start_segment].start,
                end=srt_segments[end_segment].end,
                text=content_item.text,
            )
        )
    return parsed


def match_content_to_srt_rust(
    content_items: List["ContentItem"],
    srt_segments: List["SRTSegment"],
    progress_cb: ProgressCallback | None = None,
    progress_start: int = 45,
    progress_end: int = 88,
) -> List["OutputEntry"] | None:
    """
    Try matching with Rust engine.
    Returns OutputEntry list on success, or None when Rust engine is unavailable/failed.
    """
    engine_bin = find_rust_engine_binary()
    if engine_bin is None:
        return None

    _emit_progress(progress_cb, progress_start, "Using Rust engine for timestamp alignment...")
    payload = _build_payload(content_items, srt_segments)
    try:
        completed = subprocess.run(
            [str(engine_bin)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        logger.warning("Failed to execute Rust engine: {}", exc)
        return None

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        logger.warning("Rust engine exited with code {}: {}", completed.returncode, stderr or "<no stderr>")
        return None

    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("Rust engine returned invalid JSON: {}", exc)
        return None

    if not response.get("ok"):
        logger.warning("Rust engine match failed: {}", response.get("error", "unknown error"))
        return None

    raw_entries = response.get("entries")
    if not isinstance(raw_entries, list):
        logger.warning("Rust engine response missing entries list")
        return None

    content_by_index = {item.index: item for item in content_items}
    try:
        parsed_entries = _parse_output_entries(raw_entries, content_by_index, srt_segments)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Rust engine response parse error: {}", exc)
        return None

    _emit_progress(progress_cb, progress_end, "Rust engine alignment completed.")
    return parsed_entries
