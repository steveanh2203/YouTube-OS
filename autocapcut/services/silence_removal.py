"""Silence compression using WebRTC VAD and FFmpeg chunk concat."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

from loguru import logger

from autocapcut.services.ffmpeg_utils import FFprobeError, probe_duration_microseconds
from autocapcut.services.sync_audio import AUDIO_EXTENSIONS

DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
DEFAULT_ANALYSIS_SAMPLE_RATE = 16000
DEFAULT_VAD_MODE = 2
DEFAULT_VAD_FRAME_MS = 20
DEFAULT_VAD_BRIDGE_MS = 140
DEFAULT_MIN_SPEECH_MS = 120
MAX_CUT_RATIO = 0.6
MAX_SEGMENTS_PER_MINUTE = 30.0
SHORT_SILENCE_KEEP_MAX = 0.12
MEDIUM_SILENCE_MAX = 0.5
MEDIUM_SILENCE_TARGET = 0.2
LONG_SILENCE_TARGET = 0.3


@dataclass
class SilenceRemovalSummary:
    processed: int
    output_files: int
    skipped: int
    errors: List[str]


class SilenceRemovalError(RuntimeError):
    """Raised when silence removal fails."""


ProgressCallback = Callable[[int, int, Path | None], None]


def remove_silence_batch(
    input_folder: Path,
    output_folder: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    sample_rate: int = DEFAULT_ANALYSIS_SAMPLE_RATE,
    vad_mode: int = DEFAULT_VAD_MODE,
    min_silence_duration: float = 0.24,
    padding: float = 0.2,
    min_keep_duration: float = 0.2,
    merge_gap: float = 0.08,
    progress_callback: ProgressCallback | None = None,
) -> SilenceRemovalSummary:
    """Compress silence in all audio files from *input_folder* and write to *output_folder*."""

    input_folder = input_folder.expanduser()
    output_folder = output_folder.expanduser()

    if not input_folder.exists():
        raise SilenceRemovalError(f"Input folder not found: {input_folder}")
    if not input_folder.is_dir():
        raise SilenceRemovalError("Input path must be a folder.")

    output_folder.mkdir(parents=True, exist_ok=True)

    audio_files = _collect_audio_files(input_folder)
    if not audio_files:
        raise SilenceRemovalError("No audio files found in the input folder.")

    _ensure_ffmpeg_available(ffmpeg_bin)
    _ensure_webrtcvad_available()

    if sample_rate not in (8000, 16000, 32000, 48000):
        raise SilenceRemovalError("sample_rate must be one of 8000, 16000, 32000, 48000.")
    if vad_mode not in (0, 1, 2, 3):
        raise SilenceRemovalError("vad_mode must be 0..3.")

    total = len(audio_files)
    processed = 0
    output_files = 0
    skipped = 0
    errors: List[str] = []

    for index, audio_path in enumerate(audio_files, start=1):
        if progress_callback:
            progress_callback(index - 1, total, audio_path)

        try:
            output_path = output_folder / audio_path.name
            if output_path.exists():
                raise SilenceRemovalError("Output file already exists.")

            duration_s = _probe_duration_seconds(audio_path, ffmpeg_bin=ffmpeg_bin)
            if duration_s is None or duration_s <= 0:
                raise SilenceRemovalError("Unable to determine audio duration.")

            silence_ranges = _detect_silence_ranges_webrtc(
                audio_path,
                ffmpeg_bin=ffmpeg_bin,
                sample_rate=sample_rate,
                vad_mode=vad_mode,
                frame_ms=DEFAULT_VAD_FRAME_MS,
                bridge_ms=DEFAULT_VAD_BRIDGE_MS,
                min_speech_ms=DEFAULT_MIN_SPEECH_MS,
                duration_s=duration_s,
                min_silence_duration=min_silence_duration,
            )
            keep_ranges = _build_keep_ranges(
                duration_s,
                silence_ranges,
                padding=padding,
                min_keep_duration=min_keep_duration,
                merge_gap=merge_gap,
            )

            if not keep_ranges:
                _copy_original(audio_path, output_path)
                processed += 1
                output_files += 1
                errors.append(
                    f"[NOTE] {audio_path.name}: No non-silent segments detected; copied original file unchanged."
                )
            elif _is_full_coverage(keep_ranges, duration_s):
                _copy_original(audio_path, output_path)
                processed += 1
                output_files += 1
                errors.append(
                    f"[NOTE] {audio_path.name}: No sizable silence removed; copied original file unchanged."
                )
            elif _is_overcut_or_fragmented(keep_ranges, duration_s):
                _copy_original(audio_path, output_path)
                processed += 1
                output_files += 1
                errors.append(
                    f"[NOTE] {audio_path.name}: Safety rollback triggered (over-cut risk); copied original file unchanged."
                )
            else:
                _trim_queue_concat(
                    audio_path,
                    output_path,
                    keep_ranges,
                    ffmpeg_bin=ffmpeg_bin,
                    keep_temp_chunks=False,
                )
                processed += 1
                output_files += 1
        except SilenceRemovalError as exc:
            skipped += 1
            errors.append(f"{audio_path.name}: {exc}")
        except Exception as exc:  # pragma: no cover - defensive guard
            skipped += 1
            logger.exception("Unexpected error while processing {}", audio_path.name)
            errors.append(f"{audio_path.name}: Unexpected error: {exc}")

        if progress_callback:
            progress_callback(index, total, audio_path)

    return SilenceRemovalSummary(
        processed=processed,
        output_files=output_files,
        skipped=skipped,
        errors=errors,
    )


def _collect_audio_files(folder: Path) -> List[Path]:
    return [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]


def _probe_duration_seconds(path: Path, *, ffmpeg_bin: str) -> float | None:
    try:
        return probe_duration_microseconds(path) / 1_000_000
    except FFprobeError as exc:
        logger.debug("ffprobe duration unavailable for {}: {}", path.name, exc)

    result = _run_command_capture(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-i",
            str(path),
        ],
        description=f"probe duration {path.name}",
        check=False,
    )

    match = DURATION_RE.search(result.stderr)
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _ensure_webrtcvad_available() -> None:
    try:
        import webrtcvad  # noqa: F401
    except ImportError as exc:
        raise SilenceRemovalError(
            "webrtcvad is not installed. Install `webrtcvad-wheels` or `webrtcvad` and retry."
        ) from exc


def _detect_silence_ranges_webrtc(
    audio_path: Path,
    *,
    ffmpeg_bin: str,
    sample_rate: int,
    vad_mode: int,
    frame_ms: int,
    bridge_ms: int,
    min_speech_ms: int,
    duration_s: float,
    min_silence_duration: float,
) -> List[Tuple[float, float]]:
    pcm, used_sample_rate = _read_pcm_mono_16bit(
        audio_path,
        ffmpeg_bin=ffmpeg_bin,
        sample_rate=sample_rate,
    )
    speech_ranges = _webrtc_speech_ranges(
        pcm,
        used_sample_rate,
        vad_mode=vad_mode,
        frame_ms=frame_ms,
        bridge_ms=bridge_ms,
        min_speech_ms=min_speech_ms,
    )
    speech_ranges = _merge_segments(speech_ranges, merge_gap=0.0)
    silence_ranges = _complement_ranges(speech_ranges, duration_s)

    threshold = max(min_silence_duration, SHORT_SILENCE_KEEP_MAX)
    filtered = [(start, end) for start, end in silence_ranges if end - start >= threshold]
    return _merge_segments(filtered, merge_gap=0.0)


def _webrtc_speech_ranges(
    pcm: bytes,
    sample_rate: int,
    *,
    vad_mode: int,
    frame_ms: int,
    bridge_ms: int,
    min_speech_ms: int,
) -> List[Tuple[float, float]]:
    import webrtcvad

    if sample_rate not in (8000, 16000, 32000, 48000):
        raise SilenceRemovalError("WebRTC VAD expects sample rate 8k/16k/32k/48k.")
    if frame_ms not in (10, 20, 30):
        raise SilenceRemovalError("WebRTC VAD frame size must be 10, 20, or 30 ms.")

    frame_bytes = int(sample_rate * frame_ms / 1000) * 2
    if frame_bytes <= 0:
        return []

    frame_count = len(pcm) // frame_bytes
    if frame_count <= 0:
        return []

    vad = webrtcvad.Vad(vad_mode)
    active: List[bool] = []
    for idx in range(frame_count):
        frame = pcm[idx * frame_bytes : (idx + 1) * frame_bytes]
        active.append(vad.is_speech(frame, sample_rate))

    bridge_frames = max(1, int(round(bridge_ms / frame_ms)))
    min_speech_frames = max(1, int(round(min_speech_ms / frame_ms)))

    active = _bridge_short_false_runs(active, max_run=bridge_frames)
    active = _drop_short_true_runs(active, min_run=min_speech_frames)

    frame_s = frame_ms / 1000.0
    return _bool_mask_to_ranges(active, frame_s)


def _bridge_short_false_runs(mask: Sequence[bool], *, max_run: int) -> List[bool]:
    if not mask:
        return []

    out = list(mask)
    idx = 0
    total = len(out)
    while idx < total:
        if out[idx]:
            idx += 1
            continue
        start = idx
        while idx < total and not out[idx]:
            idx += 1
        end = idx
        run_len = end - start
        has_true_left = start > 0 and out[start - 1]
        has_true_right = end < total and out[end]
        if has_true_left and has_true_right and run_len <= max_run:
            for pos in range(start, end):
                out[pos] = True
    return out


def _drop_short_true_runs(mask: Sequence[bool], *, min_run: int) -> List[bool]:
    if not mask:
        return []

    out = list(mask)
    idx = 0
    total = len(out)
    while idx < total:
        if not out[idx]:
            idx += 1
            continue
        start = idx
        while idx < total and out[idx]:
            idx += 1
        end = idx
        if end - start < min_run:
            for pos in range(start, end):
                out[pos] = False
    return out


def _bool_mask_to_ranges(mask: Sequence[bool], frame_s: float) -> List[Tuple[float, float]]:
    ranges: List[Tuple[float, float]] = []
    idx = 0
    total = len(mask)
    while idx < total:
        if not mask[idx]:
            idx += 1
            continue
        start = idx
        while idx < total and mask[idx]:
            idx += 1
        end = idx
        ranges.append((start * frame_s, end * frame_s))
    return ranges


def _complement_ranges(ranges: Sequence[Tuple[float, float]], duration_s: float) -> List[Tuple[float, float]]:
    if duration_s <= 0:
        return []

    normalized = _merge_segments(ranges, merge_gap=0.0)
    silence: List[Tuple[float, float]] = []
    cursor = 0.0
    for start, end in normalized:
        start = max(0.0, min(duration_s, start))
        end = max(0.0, min(duration_s, end))
        if start > cursor:
            silence.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration_s:
        silence.append((cursor, duration_s))
    return silence


def _build_keep_ranges(
    duration_s: float,
    silence_ranges: Sequence[Tuple[float, float]],
    *,
    padding: float,
    min_keep_duration: float,
    merge_gap: float,
) -> List[Tuple[float, float]]:
    if duration_s <= 0:
        return []

    # Compression model:
    # - keep very short pauses intact
    # - reduce medium pauses to ~160ms
    # - reduce long pauses to ~200ms
    # We remove only the center of silence to avoid clipping spoken boundaries.
    normalized_silence = _merge_segments(silence_ranges, merge_gap=0.0)
    remove_ranges: List[Tuple[float, float]] = []

    for silence_start, silence_end in normalized_silence:
        silence_start = max(0.0, min(duration_s, silence_start))
        silence_end = max(0.0, min(duration_s, silence_end))
        silence_duration = silence_end - silence_start
        if silence_duration <= SHORT_SILENCE_KEEP_MAX:
            continue

        target_keep = (
            MEDIUM_SILENCE_TARGET
            if silence_duration <= MEDIUM_SILENCE_MAX
            else LONG_SILENCE_TARGET
        )
        edge_keep = max(0.0, min(padding, silence_duration / 2.0))
        required_keep = max(target_keep, edge_keep * 2.0)
        if required_keep >= silence_duration:
            continue

        keep_each_side = required_keep / 2.0
        remove_start = silence_start + keep_each_side
        remove_end = silence_end - keep_each_side
        remove_start = max(remove_start, silence_start + edge_keep)
        remove_end = min(remove_end, silence_end - edge_keep)
        if remove_end - remove_start > 0.005:
            remove_ranges.append((remove_start, remove_end))

    if not remove_ranges:
        return [(0.0, duration_s)]

    remove_ranges = _merge_segments(remove_ranges, merge_gap=0.0)
    keep_ranges: List[Tuple[float, float]] = []
    cursor = 0.0
    for cut_start, cut_end in remove_ranges:
        if cut_start > cursor:
            keep_ranges.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < duration_s:
        keep_ranges.append((cursor, duration_s))

    filtered: List[Tuple[float, float]] = []
    min_effective = min(0.03, max(0.0, min_keep_duration / 10.0))
    for start, end in keep_ranges:
        if end - start >= min_effective:
            filtered.append((start, end))
    return _merge_segments(filtered, merge_gap=max(0.0, merge_gap))


def _is_overcut_or_fragmented(segments: Sequence[Tuple[float, float]], duration_s: float) -> bool:
    if duration_s <= 0 or not segments:
        return False

    kept = 0.0
    for start, end in segments:
        if end > start:
            kept += end - start
    cut_ratio = max(0.0, 1.0 - (kept / duration_s))
    if cut_ratio > MAX_CUT_RATIO:
        return True

    if duration_s >= 60.0:
        segments_per_minute = len(segments) / max(duration_s / 60.0, 0.01)
        return segments_per_minute > MAX_SEGMENTS_PER_MINUTE
    return False


def _is_full_coverage(segments: Sequence[Tuple[float, float]], duration_s: float) -> bool:
    if duration_s <= 0 or not segments:
        return False
    merged = _merge_segments(segments, merge_gap=0.0)
    if len(merged) != 1:
        return False
    start, end = merged[0]
    return start <= 0.02 and end >= duration_s - 0.02


def _merge_segments(
    segments: Sequence[Tuple[float, float]],
    merge_gap: float,
) -> List[Tuple[float, float]]:
    if not segments:
        return []

    ordered = sorted(segments, key=lambda item: item[0])
    merged: List[Tuple[float, float]] = [ordered[0]]

    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + merge_gap:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _read_pcm_mono_16bit(
    input_path: Path,
    *,
    ffmpeg_bin: str,
    sample_rate: int,
) -> Tuple[bytes, int]:
    with tempfile.TemporaryDirectory(prefix="autocapcut_vad_pcm_") as temp_dir:
        wav_path = Path(temp_dir) / "analysis.wav"
        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "wav",
            str(wav_path),
        ]
        _run_command(cmd, description=f"prepare VAD PCM {input_path.name}")
        with wave.open(str(wav_path), "rb") as wf:
            if wf.getnchannels() != 1:
                raise SilenceRemovalError("Expected mono WAV for VAD analysis.")
            if wf.getsampwidth() != 2:
                raise SilenceRemovalError("Expected 16-bit PCM WAV for VAD analysis.")
            sr = wf.getframerate()
            pcm_data = wf.readframes(wf.getnframes())
    return pcm_data, sr


def _trim_queue_concat(
    input_path: Path,
    output_path: Path,
    keep_ranges: Sequence[Tuple[float, float]],
    *,
    ffmpeg_bin: str,
    keep_temp_chunks: bool,
) -> None:
    if not keep_ranges:
        raise SilenceRemovalError("No non-silent segments to render.")

    with tempfile.TemporaryDirectory(prefix=f"autocapcut_chunks_{input_path.stem}_") as tmp_dir:
        temp_dir = Path(tmp_dir)
        chunk_paths: List[Path] = []

        for idx, (start, end) in enumerate(keep_ranges, start=1):
            if end <= start:
                continue
            chunk_path = temp_dir / f"{input_path.stem}_{idx:04d}.wav"
            _render_chunk(
                input_path,
                chunk_path,
                start=start,
                end=end,
                ffmpeg_bin=ffmpeg_bin,
            )
            if chunk_path.exists() and chunk_path.stat().st_size > 44:
                chunk_paths.append(chunk_path)

        if not chunk_paths:
            raise SilenceRemovalError("No chunks were produced after trimming.")

        manifest_path = temp_dir / "concat_list.txt"
        _write_concat_manifest(manifest_path, chunk_paths)

        merged_wav = temp_dir / "merged.wav"
        _concat_wav_chunks(manifest_path, merged_wav, ffmpeg_bin=ffmpeg_bin)

        _encode_final_audio(merged_wav, output_path, ffmpeg_bin=ffmpeg_bin)

        if keep_temp_chunks:
            debug_dir = output_path.parent / f"{output_path.stem}_chunks"
            debug_dir.mkdir(parents=True, exist_ok=True)
            for chunk in chunk_paths:
                shutil.copy2(chunk, debug_dir / chunk.name)


def _render_chunk(
    input_path: Path,
    chunk_path: Path,
    *,
    start: float,
    end: float,
    ffmpeg_bin: str,
) -> None:
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-ss",
        f"{start:.6f}",
        "-to",
        f"{end:.6f}",
        "-vn",
        "-sn",
        "-dn",
        "-c:a",
        "pcm_s16le",
        str(chunk_path),
    ]
    _run_command(cmd, description=f"render chunk {chunk_path.name}")


def _write_concat_manifest(manifest_path: Path, chunk_paths: Sequence[Path]) -> None:
    lines: List[str] = []
    for path in chunk_paths:
        escaped = str(path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    manifest_path.write_text("\n".join(lines), encoding="utf-8")


def _concat_wav_chunks(manifest_path: Path, merged_wav: Path, *, ffmpeg_bin: str) -> None:
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest_path),
        "-vn",
        "-sn",
        "-dn",
        "-c:a",
        "pcm_s16le",
        str(merged_wav),
    ]
    _run_command(cmd, description="concat chunks")


def _encode_final_audio(merged_wav: Path, output_path: Path, *, ffmpeg_bin: str) -> None:
    suffix = output_path.suffix.lower()

    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(merged_wav),
        "-vn",
        "-sn",
        "-dn",
    ]

    if suffix == ".wav":
        cmd.extend(["-c:a", "pcm_s16le"])
    elif suffix == ".mp3":
        cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])
    elif suffix == ".m4a":
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"])
    elif suffix == ".aac":
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    elif suffix == ".flac":
        cmd.extend(["-c:a", "flac"])
    else:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])

    cmd.append(str(output_path))
    _run_command(cmd, description=f"encode {output_path.name}")


def _copy_original(input_path: Path, output_path: Path) -> None:
    try:
        shutil.copy2(input_path, output_path)
    except OSError as exc:
        raise SilenceRemovalError(f"Unable to copy original file: {exc}") from exc


def _ensure_ffmpeg_available(ffmpeg_bin: str) -> None:
    if shutil.which(ffmpeg_bin) is None:
        raise SilenceRemovalError("FFmpeg executable not found. Install FFmpeg and ensure it is in PATH.")


def _run_command(cmd: List[str], *, description: str) -> None:
    logger.debug("Running FFmpeg command ({}): {}", description, " ".join(cmd))
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SilenceRemovalError("FFmpeg executable not found. Install FFmpeg and ensure it is in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("FFmpeg failed ({}): {}", description, exc.stderr)
        raise SilenceRemovalError(f"FFmpeg failed during {description}: {exc.stderr.strip()}") from exc
    else:
        if result.stderr:
            logger.debug(result.stderr)


def _run_command_capture(
    cmd: List[str],
    *,
    description: str,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    logger.debug("Running command ({}): {}", description, " ".join(cmd))
    try:
        result = subprocess.run(cmd, check=check, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SilenceRemovalError("FFmpeg executable not found. Install FFmpeg and ensure it is in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("Command failed ({}): {}", description, exc.stderr)
        raise SilenceRemovalError(f"FFmpeg failed during {description}: {exc.stderr.strip()}") from exc
    return result
