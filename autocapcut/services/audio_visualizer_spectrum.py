"""Preview-parity spectrum renderer for backend video export."""
from __future__ import annotations

import math
import signal
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from numba import njit
from PIL import Image


DEFAULT_SAMPLE_RATE = 44_100
DEFAULT_FFT_SIZE = 2048
DEFAULT_RENDER_BATCH_FRAMES = 600
DEFAULT_MIN_DECIBELS = -92.0
DEFAULT_MAX_DECIBELS = -18.0
SPECTRUM_FREQ_ATTACK = 0.64
SPECTRUM_FREQ_RELEASE = 0.34
SPECTRUM_MONSTERCAT_AMOUNT = 2.55
SPECTRUM_BAR_ATTACK = 0.84
SPECTRUM_BAR_RELEASE = 0.22
SPECTRUM_BAR_SHAPE_BLEND = 0.14
SPECTRUM_PEAK_RISE = 0.02
SPECTRUM_PEAK_DECAY = 0.03


@dataclass(frozen=True)
class SpectrumLayout:
    overlay_width: int
    overlay_height: int
    overlay_x: int
    overlay_y: int
    bins: int
    bar_width: int
    gap: int
    pitch: int
    total_width: int
    center_gap: int
    max_height: int
    min_height: int
    top_base_y: int
    bottom_base_y: int


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def brighten_hex(color: str, amount: float = 0.35) -> tuple[int, int, int]:
    rgb = hex_to_rgb(color)
    return tuple(min(255, round(channel + (255 - channel) * amount)) for channel in rgb)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_with_alpha_over_black(color: tuple[int, int, int], alpha: int) -> tuple[int, int, int]:
    factor = alpha / 255.0
    return tuple(max(0, min(255, round(channel * factor))) for channel in color)


def build_spectrum_layout(width: int, height: int) -> SpectrumLayout:
    bins = max(72, min(118, width // 14))
    bar_width = max(5, (width // bins) - 3)
    gap = 2
    pitch = bar_width + gap
    total_width = bins * pitch - gap
    center_gap = max(2, int(height * 0.18))
    max_height = max(24, int(height * 0.2))
    min_height = 6
    overlay_height = max_height * 2 + center_gap
    overlay_x = max(0, (width - total_width) // 2)
    overlay_y = max(0, (height - overlay_height) // 2)
    top_base_y = max_height
    bottom_base_y = max_height + center_gap
    return SpectrumLayout(
        overlay_width=total_width,
        overlay_height=overlay_height,
        overlay_x=overlay_x,
        overlay_y=overlay_y,
        bins=bins,
        bar_width=bar_width,
        gap=gap,
        pitch=pitch,
        total_width=total_width,
        center_gap=center_gap,
        max_height=max_height,
        min_height=min_height,
        top_base_y=top_base_y,
        bottom_base_y=bottom_base_y,
    )


def blur_bins(values: np.ndarray) -> np.ndarray:
    out = np.empty_like(values)
    count = len(values)
    for i in range(count):
        left2 = values[max(0, i - 2)]
        left1 = values[max(0, i - 1)]
        center = values[i]
        right1 = values[min(count - 1, i + 1)]
        right2 = values[min(count - 1, i + 2)]
        out[i] = left2 * 0.02 + left1 * 0.12 + center * 0.72 + right1 * 0.12 + right2 * 0.02
    return out


def apply_monstercat(values: np.ndarray, amount: float = 3.4) -> np.ndarray:
    out = values.astype(np.float64, copy=True)
    count = len(out)
    for z in range(count):
        for y in range(z - 1, -1, -1):
            distance = z - y
            out[y] = max(out[y], out[z] / math.pow(amount * 1.5, distance))
        for y in range(z + 1, count):
            distance = y - z
            out[y] = max(out[y], out[z] / math.pow(amount * 1.5, distance))
    return out.astype(np.float32)


def sample_spectrum_bins(source: np.ndarray, bins: int) -> np.ndarray:
    out = np.zeros(bins, dtype=np.float32)
    max_index = len(source) - 1
    frame_peak = 0.0

    for i in range(bins):
        start_t = i / max(1, bins - 1)
        end_t = (i + 1) / bins
        map_start = start_t * 0.58 + math.pow(start_t, 1.12) * 0.42
        map_end = end_t * 0.58 + math.pow(end_t, 1.12) * 0.42
        start_idx = int(math.floor(map_start * max_index))
        end_idx = max(start_idx + 1, int(math.floor(map_end * max_index)))

        weighted_sum = 0.0
        total_weight = 0.0
        slice_peak = 0.0
        for idx in range(start_idx, min(end_idx, max_index) + 1):
            position = 0.0 if max_index <= 0 else idx / max_index
            psycho_weight = 0.9 + position * 1.45
            weighted = float(source[idx]) * psycho_weight
            weighted_sum += weighted
            total_weight += psycho_weight
            slice_peak = max(slice_peak, weighted)

        avg = (weighted_sum / total_weight) if total_weight > 0 else 0.0
        blended = avg * 0.45 + slice_peak * 0.55
        edge_compensation = 0.96 + math.pow(i / max(1, bins - 1), 1.18) * 1.15
        compensated = blended * edge_compensation
        out[i] = compensated
        frame_peak = max(frame_peak, compensated)

    if frame_peak <= 0:
        return out

    envelope = blur_bins(out)
    floor = frame_peak * 0.05
    normalized = np.zeros(bins, dtype=np.float32)
    avg_lifted = 0.0
    for i in range(bins):
        lifted = clamp01((float(out[i]) - floor) / max(1e-5, frame_peak - floor))
        normalized[i] = lifted
        avg_lifted += lifted
    avg_lifted /= max(1, bins)

    for i in range(bins):
        lifted = float(normalized[i])
        local_envelope = max(float(envelope[i]), frame_peak * 0.14)
        local_contrast = clamp01(float(out[i]) / local_envelope)
        position = i / max(1, bins - 1)
        distance_from_center = abs(position - 0.5) / 0.5
        center_focus = 1 - math.pow(clamp01(distance_from_center), 1.25)
        belly_envelope = 0.18 + center_focus * 0.92
        shared_motion = avg_lifted * (0.2 + center_focus * 0.55)
        edge_drop = 0.02 + center_focus * 0.06
        center_lift = center_focus * (0.12 + avg_lifted * 0.28)
        local_detail = local_contrast * (0.05 + center_focus * 0.16)
        spread = lifted * (0.26 + center_focus * 0.34) + shared_motion * 0.28 + local_detail
        rhythmic_floor = shared_motion * (0.54 + center_focus * 0.26) + center_lift
        out[i] = math.pow(clamp01((max(spread, rhythmic_floor) + edge_drop) * belly_envelope), 0.98)

    return out


def decode_audio_mono(audio_path: str, sample_rate: int = DEFAULT_SAMPLE_RATE) -> np.ndarray:
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", audio_path,
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "f32le",
        "pipe:1",
    ]
    pcm = subprocess.check_output(cmd)
    return np.frombuffer(pcm, dtype=np.float32)


@lru_cache(maxsize=8)
def analysis_window(size: int = DEFAULT_FFT_SIZE) -> np.ndarray:
    return np.hanning(size).astype(np.float32)


def current_spectrum_batch(
    samples: np.ndarray,
    frame_start: int,
    frame_count: int,
    fps: int,
) -> np.ndarray:
    if frame_count <= 0:
        return np.zeros((0, DEFAULT_FFT_SIZE // 2 + 1), dtype=np.float32)

    hop = DEFAULT_SAMPLE_RATE / fps
    ends = ((np.arange(frame_start, frame_start + frame_count, dtype=np.float64) + 1.0) * hop).astype(np.int64)
    offsets = np.arange(DEFAULT_FFT_SIZE, dtype=np.int64)
    indices = ends[:, None] - DEFAULT_FFT_SIZE + offsets[None, :]

    frames = np.zeros((frame_count, DEFAULT_FFT_SIZE), dtype=np.float32)
    if samples.size > 0:
        valid = (indices >= 0) & (indices < samples.shape[0])
        safe_indices = np.clip(indices, 0, max(samples.shape[0] - 1, 0))
        frames[valid] = samples[safe_indices[valid]]

    windowed = frames * analysis_window()[None, :]
    fft_mag = np.abs(np.fft.rfft(windowed, axis=1))
    db = 20.0 * np.log10(fft_mag + 1e-6)
    return np.clip((db - DEFAULT_MIN_DECIBELS) / (DEFAULT_MAX_DECIBELS - DEFAULT_MIN_DECIBELS), 0.0, 1.0).astype(np.float32) * 255.0


@njit(cache=True)
def clamp01_nb(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


@njit(cache=True)
def blur_bins_nb(values: np.ndarray) -> np.ndarray:
    out = np.empty_like(values)
    count = values.shape[0]
    for i in range(count):
        left2 = values[0 if i - 2 < 0 else i - 2]
        left1 = values[0 if i - 1 < 0 else i - 1]
        center = values[i]
        right1 = values[count - 1 if i + 1 >= count else i + 1]
        right2 = values[count - 1 if i + 2 >= count else i + 2]
        out[i] = left2 * 0.02 + left1 * 0.12 + center * 0.72 + right1 * 0.12 + right2 * 0.02
    return out


@njit(cache=True)
def apply_monstercat_nb(values: np.ndarray, amount: float) -> np.ndarray:
    out = values.copy()
    count = out.shape[0]
    falloff = amount * 1.5
    for z in range(count):
        for y in range(z - 1, -1, -1):
            distance = z - y
            candidate = out[z] / math.pow(falloff, distance)
            if candidate > out[y]:
                out[y] = candidate
        for y in range(z + 1, count):
            distance = y - z
            candidate = out[z] / math.pow(falloff, distance)
            if candidate > out[y]:
                out[y] = candidate
    return out


@njit(cache=True)
def sample_spectrum_bins_nb(source: np.ndarray, bins: int) -> np.ndarray:
    out = np.zeros(bins, dtype=np.float32)
    max_index = source.shape[0] - 1
    frame_peak = 0.0

    for i in range(bins):
        start_t = i / max(1, bins - 1)
        end_t = (i + 1) / bins
        map_start = start_t * 0.58 + math.pow(start_t, 1.12) * 0.42
        map_end = end_t * 0.58 + math.pow(end_t, 1.12) * 0.42
        start_idx = int(math.floor(map_start * max_index))
        end_idx = max(start_idx + 1, int(math.floor(map_end * max_index)))

        weighted_sum = 0.0
        total_weight = 0.0
        slice_peak = 0.0
        upper = min(end_idx, max_index)
        for idx in range(start_idx, upper + 1):
            position = 0.0 if max_index <= 0 else idx / max_index
            psycho_weight = 0.9 + position * 1.45
            weighted = float(source[idx]) * psycho_weight
            weighted_sum += weighted
            total_weight += psycho_weight
            if weighted > slice_peak:
                slice_peak = weighted

        avg = (weighted_sum / total_weight) if total_weight > 0.0 else 0.0
        blended = avg * 0.45 + slice_peak * 0.55
        edge_compensation = 0.96 + math.pow(i / max(1, bins - 1), 1.18) * 1.15
        compensated = blended * edge_compensation
        out[i] = compensated
        if compensated > frame_peak:
            frame_peak = compensated

    if frame_peak <= 0.0:
        return out

    envelope = blur_bins_nb(out)
    floor = frame_peak * 0.05
    normalized = np.zeros(bins, dtype=np.float32)
    avg_lifted = 0.0
    for i in range(bins):
        lifted = clamp01_nb((float(out[i]) - floor) / max(1e-5, frame_peak - floor))
        normalized[i] = lifted
        avg_lifted += lifted
    avg_lifted /= max(1, bins)

    for i in range(bins):
        lifted = float(normalized[i])
        local_envelope = max(float(envelope[i]), frame_peak * 0.14)
        local_contrast = clamp01_nb(float(out[i]) / local_envelope)
        position = i / max(1, bins - 1)
        distance_from_center = abs(position - 0.5) / 0.5
        center_focus = 1.0 - math.pow(clamp01_nb(distance_from_center), 1.25)
        belly_envelope = 0.18 + center_focus * 0.92
        shared_motion = avg_lifted * (0.2 + center_focus * 0.55)
        edge_drop = 0.02 + center_focus * 0.06
        center_lift = center_focus * (0.12 + avg_lifted * 0.28)
        local_detail = local_contrast * (0.05 + center_focus * 0.16)
        spread = lifted * (0.26 + center_focus * 0.34) + shared_motion * 0.28 + local_detail
        rhythmic_floor = shared_motion * (0.54 + center_focus * 0.26) + center_lift
        out[i] = math.pow(clamp01_nb((max(spread, rhythmic_floor) + edge_drop) * belly_envelope), 0.98)

    return out


@njit(cache=True)
def process_spectrum_batch(
    currents: np.ndarray,
    bins: int,
    smooth_freq: np.ndarray,
    bars_state: np.ndarray,
    peaks_state: np.ndarray,
    has_smooth: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    frame_count = currents.shape[0]
    freq_count = currents.shape[1]
    bars_out = np.empty((frame_count, bins), dtype=np.float32)
    peaks_out = np.empty((frame_count, bins), dtype=np.float32)
    smooth = smooth_freq.copy()
    bars = bars_state.copy()
    peaks = peaks_state.copy()
    scratch = np.empty(bins, dtype=np.float32)

    for frame_idx in range(frame_count):
        if not has_smooth and frame_idx == 0:
            for k in range(freq_count):
                smooth[k] = currents[frame_idx, k]
        else:
            for k in range(freq_count):
                current = currents[frame_idx, k]
                prev_smooth = smooth[k]
                factor = SPECTRUM_FREQ_ATTACK if current > prev_smooth else SPECTRUM_FREQ_RELEASE
                smooth[k] = prev_smooth + (current - prev_smooth) * factor

        sampled = sample_spectrum_bins_nb(smooth, bins)
        energized = apply_monstercat_nb(sampled, SPECTRUM_MONSTERCAT_AMOUNT)
        blurred = blur_bins_nb(energized)

        for i in range(bins):
            prev = float(bars[i])
            prev_sample = float(sampled[0 if i - 1 < 0 else i - 1])
            next_sample = float(sampled[bins - 1 if i + 1 >= bins else i + 1])
            local_delta = float(sampled[i]) - ((prev_sample + next_sample) * 0.5)
            micro_detail = clamp01_nb(float(sampled[i]) + local_delta * 0.55)
            left_blur = float(blurred[0 if i - 1 < 0 else i - 1])
            right_blur = float(blurred[bins - 1 if i + 1 >= bins else i + 1])
            near_avg = (left_blur + float(blurred[i]) + right_blur) / 3.0
            transient_boost = max(0.0, float(sampled[i]) - prev) * 0.12
            target = clamp01_nb(near_avg * 0.74 + micro_detail * 0.26 + transient_boost)
            attack = SPECTRUM_BAR_ATTACK
            release = SPECTRUM_BAR_RELEASE
            next_value = prev + (target - prev) * (attack if target > prev else release)
            scratch[i] = next_value * 0.88 + target * 0.12

        for i in range(bins):
            left = float(scratch[0 if i - 1 < 0 else i - 1])
            center = float(scratch[i])
            right = float(scratch[bins - 1 if i + 1 >= bins else i + 1])
            shaped = center * (1.0 - SPECTRUM_BAR_SHAPE_BLEND) + ((left + center + right) / 3.0) * SPECTRUM_BAR_SHAPE_BLEND
            bars[i] = shaped

            peak_target = shaped + SPECTRUM_PEAK_RISE
            peaks[i] = peak_target if peak_target > peaks[i] else max(shaped, float(peaks[i]) - SPECTRUM_PEAK_DECAY)
            bars_out[frame_idx, i] = shaped
            peaks_out[frame_idx, i] = peaks[i]

        has_smooth = True

    return bars_out, peaks_out, smooth, bars, peaks


def iter_spectrum_states(
    audio_path: str,
    layout: SpectrumLayout,
    fps: int,
) -> tuple[int, np.ndarray, np.ndarray]:
    samples = decode_audio_mono(audio_path)
    total_frames = max(1, int(math.ceil((len(samples) / DEFAULT_SAMPLE_RATE) * fps)))
    smooth_freq = np.zeros(DEFAULT_FFT_SIZE // 2 + 1, dtype=np.float32)
    bars_state = np.zeros(layout.bins, dtype=np.float32)
    peaks_state = np.zeros(layout.bins, dtype=np.float32)
    has_smooth = False

    for frame_start in range(0, total_frames, DEFAULT_RENDER_BATCH_FRAMES):
        frame_count = min(DEFAULT_RENDER_BATCH_FRAMES, total_frames - frame_start)
        currents = current_spectrum_batch(samples, frame_start, frame_count, fps)
        bars_batch, peaks_batch, smooth_freq, bars_state, peaks_state = process_spectrum_batch(
            currents,
            layout.bins,
            smooth_freq,
            bars_state,
            peaks_state,
            has_smooth,
        )
        has_smooth = True

        for local_idx in range(frame_count):
            yield total_frames, bars_batch[local_idx], peaks_batch[local_idx]


@lru_cache(maxsize=1024)
def gradient_pixels(width: int, height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> np.ndarray:
    if height <= 0:
        return np.full((1, max(1, width), 3), bottom, dtype=np.uint8)
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        t = 0.0 if height <= 1 else y / (height - 1)
        arr[y, :, 0] = round(top[0] + (bottom[0] - top[0]) * t)
        arr[y, :, 1] = round(top[1] + (bottom[1] - top[1]) * t)
        arr[y, :, 2] = round(top[2] + (bottom[2] - top[2]) * t)
    return arr


@lru_cache(maxsize=64)
def spectrum_frame_base(
    overlay_width: int,
    overlay_height: int,
    top_base_y: int,
    bottom_base_y: int,
    line_rgb: tuple[int, int, int],
) -> np.ndarray:
    base = np.zeros((overlay_height, overlay_width, 3), dtype=np.uint8)
    base[top_base_y:top_base_y + 1, :, :] = np.array(line_rgb, dtype=np.uint8)
    base[bottom_base_y:bottom_base_y + 1, :, :] = np.array(line_rgb, dtype=np.uint8)
    return base


def blit_pixels(frame: np.ndarray, patch: np.ndarray, y: int, x: int) -> None:
    frame_h, frame_w = frame.shape[0], frame.shape[1]
    patch_h, patch_w = patch.shape[0], patch.shape[1]
    y1 = max(0, y)
    x1 = max(0, x)
    y2 = min(frame_h, y + patch_h)
    x2 = min(frame_w, x + patch_w)
    if y1 >= y2 or x1 >= x2:
        return

    patch_y1 = y1 - y
    patch_x1 = x1 - x
    patch_y2 = patch_y1 + (y2 - y1)
    patch_x2 = patch_x1 + (x2 - x1)
    frame[y1:y2, x1:x2, :] = patch[patch_y1:patch_y2, patch_x1:patch_x2, :]


def render_spectrum_frame_pixels(
    layout: SpectrumLayout,
    bars: np.ndarray,
    peaks: np.ndarray,
    color_hex: str,
) -> np.ndarray:
    vis_rgb = hex_to_rgb(color_hex)
    tip_rgb = brighten_hex(color_hex, 0.32)
    base_rgb = rgb_with_alpha_over_black(vis_rgb, 0x50)
    bar_rgb = rgb_with_alpha_over_black(vis_rgb, 0xD8)
    line_rgb = rgb_with_alpha_over_black(brighten_hex(color_hex, 0.18), 0x66)

    frame = spectrum_frame_base(
        layout.overlay_width,
        layout.overlay_height,
        layout.top_base_y,
        layout.bottom_base_y,
        line_rgb,
    ).copy()

    for i in range(layout.bins):
        eased = math.pow(clamp01(float(bars[i])), 0.92)
        peak = math.pow(clamp01(float(peaks[i])), 0.9)
        height = int(round(layout.min_height + eased * layout.max_height))
        peak_height = int(round(layout.min_height + peak * layout.max_height))
        x = i * layout.pitch
        x2 = x + layout.bar_width

        frame[layout.top_base_y - layout.min_height:layout.top_base_y, x:x2, :] = base_rgb
        frame[layout.bottom_base_y:layout.bottom_base_y + layout.min_height, x:x2, :] = base_rgb

        if height > layout.min_height + 2:
            body_height = height - layout.min_height + 1
            top_grad = gradient_pixels(layout.bar_width, body_height, tip_rgb, bar_rgb)
            bottom_grad = gradient_pixels(layout.bar_width, body_height, bar_rgb, tip_rgb)
            blit_pixels(frame, top_grad, layout.top_base_y - height, x)
            blit_pixels(frame, bottom_grad, layout.bottom_base_y + layout.min_height - 1, x)
            blit_pixels(frame, np.full((1, layout.bar_width, 3), tip_rgb, dtype=np.uint8), layout.top_base_y - height, x)
            frame[layout.bottom_base_y + height - 2:layout.bottom_base_y + height - 1, x:x2, :] = tip_rgb

        if peak_height > height + 4:
            blit_pixels(frame, np.full((1, layout.bar_width, 3), tip_rgb, dtype=np.uint8), layout.top_base_y - peak_height, x)
            frame[layout.bottom_base_y + peak_height - 2:layout.bottom_base_y + peak_height - 1, x:x2, :] = tip_rgb

    return frame


def render_spectrum_frame(
    layout: SpectrumLayout,
    bars: np.ndarray,
    peaks: np.ndarray,
    color_hex: str,
) -> Image.Image:
    return Image.fromarray(render_spectrum_frame_pixels(layout, bars, peaks, color_hex), mode="RGB")


@lru_cache(maxsize=1)
def _ffmpeg_encoders() -> set[str]:
    try:
        encoders = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        return set()

    results: set[str] = set()
    for line in encoders.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            results.add(parts[1])
    return results


def _overlay_encoder_args() -> list[str]:
    if "h264_videotoolbox" in _ffmpeg_encoders():
        return [
            "-c:v", "h264_videotoolbox",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-realtime", "1",
            "-prio_speed", "1",
            "-allow_sw", "1",
            "-b:v", "6M",
            "-maxrate", "8M",
            "-bufsize", "12M",
            "-g", "60",
        ]
    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-g", "60",
    ]


def render_spectrum_overlay_video(
    audio_path: str,
    output_path: str,
    width: int,
    height: int,
    color_hex: str,
    fps: int,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> SpectrumLayout:
    layout = build_spectrum_layout(width, height)

    cmd = [
        "ffmpeg", "-y",
        "-v", "error",
        "-nostats",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{layout.overlay_width}x{layout.overlay_height}",
        "-r", str(fps),
        "-i", "pipe:0",
        "-an",
        *_overlay_encoder_args(),
        output_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    try:
        for index, (total, bars, peaks) in enumerate(iter_spectrum_states(audio_path, layout, fps)):
            frame_pixels = render_spectrum_frame_pixels(layout, bars, peaks, color_hex)
            proc.stdin.write(frame_pixels.tobytes())
            if progress_cb and (index % 5 == 0 or index == total - 1):
                progress_cb(int(((index + 1) / max(1, total)) * 100))
    finally:
        proc.stdin.close()

    stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"Overlay render failed ({rc}): {stderr[-500:]}")
    return layout


def render_spectrum_final_video(
    audio_path: str,
    ffmpeg_cmd: list[str],
    width: int,
    height: int,
    color_hex: str,
    fps: int,
    progress_cb: Optional[Callable[[int], None]] = None,
    proc_cb: Optional[Callable[[subprocess.Popen[bytes]], None]] = None,
) -> SpectrumLayout:
    layout = build_spectrum_layout(width, height)
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc_cb is not None:
        proc_cb(proc)
    assert proc.stdin is not None

    total = 0
    try:
        for index, (total, bars, peaks) in enumerate(iter_spectrum_states(audio_path, layout, fps)):
            frame_pixels = render_spectrum_frame_pixels(layout, bars, peaks, color_hex)
            try:
                proc.stdin.write(frame_pixels.tobytes())
            except BrokenPipeError:
                break
            if progress_cb and (index % 5 == 0 or index == total - 1):
                progress_cb(int(((index + 1) / max(1, total)) * 95))
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass

    stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        if rc < 0:
            signal_name = ""
            try:
                signal_name = f" ({signal.Signals(-rc).name})"
            except Exception:
                signal_name = ""
            raise RuntimeError(f"Final spectrum render terminated{signal_name}: {stderr[-500:]}")
        raise RuntimeError(f"Final spectrum render failed ({rc}): {stderr[-500:]}")
    return layout
