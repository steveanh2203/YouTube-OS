"""Sora Gen service — API key management, video persistence, auto-rename."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import httpx
from loguru import logger

# API key lưu tại ~/.autocapcut/sora_key.txt
_KEY_PATH = Path.home() / ".autocapcut" / "sora_key.txt"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
MANAGED_VIDEO_RE = re.compile(r"^video_(\d{3})(?:_\d{2})?\.[a-z0-9]+$", re.IGNORECASE)


def target_resolution_for_ratio(ratio: str | None) -> tuple[int, int]:
    normalized = str(ratio or "").strip()
    if normalized == "9:16":
        return (1080, 1920)
    if normalized == "1:1":
        return (1080, 1080)
    return (1920, 1080)


def _video_encoder() -> list[str]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    try:
        output = subprocess.check_output([ffmpeg, "-hide_banner", "-encoders"], text=True, stderr=subprocess.STDOUT)
    except Exception:
        return ["libx264", "-preset", "fast", "-crf", "20"]

    if "h264_videotoolbox" in output:
        return ["h264_videotoolbox", "-b:v", "8M"]
    return ["libx264", "-preset", "fast", "-crf", "20"]


# ---------------------------------------------------------------------------
# API Key
# ---------------------------------------------------------------------------

def get_or_create_api_key() -> str:
    """Trả về API key hiện tại. Tạo mới nếu chưa có."""
    if _KEY_PATH.exists():
        key = _KEY_PATH.read_text().strip()
        if key:
            return key
    key = uuid.uuid4().hex
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEY_PATH.write_text(key)
    logger.info("Sora API key generated: {}", key[:8] + "...")
    return key


def validate_api_key(key: str) -> bool:
    return key == get_or_create_api_key()


def mask_api_key(key: str) -> str:
    value = (key or "").strip()
    if len(value) <= 8:
        return value
    return f"{value[:4]}...{value[-4:]}"


# ---------------------------------------------------------------------------
# Download + Auto-rename
# ---------------------------------------------------------------------------

def next_video_filename(folder: Path) -> str:
    """
    Scan folder, tìm pattern video_\\d{3}.mp4, trả về tên tiếp theo.
    Ví dụ: video_001.mp4, video_002.mp4, ...
    """
    existing = set()
    pattern = re.compile(r"^video_(\d{3})\.mp4$", re.IGNORECASE)
    for f in folder.iterdir():
        m = pattern.match(f.name)
        if m:
            existing.add(int(m.group(1)))

    n = 1
    while n in existing:
        n += 1
    return f"video_{n:03d}.mp4"


def list_video_files(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted(
        [
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        ],
        key=lambda item: item.name.lower(),
    )


def clear_managed_videos(folder: Path) -> int:
    removed = 0
    for path in list_video_files(folder):
        if not MANAGED_VIDEO_RE.match(path.name):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def next_video_path(folder: Path, output_index: int | None = None, suffix: str = ".mp4") -> Path:
    suffix = (suffix or ".mp4").strip().lower()
    if not suffix.startswith("."):
        suffix = f".{suffix}"

    if output_index is None:
        return (folder / next_video_filename(folder)).with_suffix(suffix)

    index = max(1, int(output_index))
    base = f"video_{index:03d}"
    candidate = folder / f"{base}{suffix}"
    if not candidate.exists():
        return candidate

    # Không overwrite video cũ âm thầm. Giữ index chính, thêm suffix tránh đụng file.
    copy_index = 2
    while True:
        candidate = folder / f"{base}_{copy_index:02d}{suffix}"
        if not candidate.exists():
            return candidate
        copy_index += 1


def save_uploaded_video(
    content: bytes,
    dest_folder: Path,
    suffix: str = ".mp4",
    output_index: int | None = None,
) -> Path:
    """
    Persist an uploaded/generated MP4 blob into the managed folder with the next
    sequential filename.
    """
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest = next_video_path(dest_folder, output_index=output_index, suffix=suffix)
    dest.write_bytes(content)
    logger.info("Sora uploaded video saved: {} ({:.1f} MB)", dest, dest.stat().st_size / 1_048_576)
    return dest


async def download_video(url: str, dest_folder: Path, output_index: int | None = None) -> Path:
    """
    Download MP4 từ URL về dest_folder.
    Tự đặt tên video001.mp4, video002.mp4, ...
    Trả về Path của file đã lưu.
    """
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest = next_video_path(dest_folder, output_index=output_index, suffix=".mp4")

    logger.info("Downloading Sora video → {}", dest)

    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=1024 * 256):
                    f.write(chunk)

    logger.info("Sora video saved: {} ({:.1f} MB)", dest, dest.stat().st_size / 1_048_576)
    return dest


def enhance_video_1080p(
    source_path: Path,
    *,
    ratio: str | None = None,
    destination_path: Path | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    source = Path(source_path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"Source video not found: {source}")

    target_w, target_h = target_resolution_for_ratio(ratio)
    if destination_path is None:
        destination = source.with_name(f"{source.stem}_1080p{source.suffix}")
    else:
        destination = Path(destination_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = Path(tempfile.mkdtemp(prefix="sora_enhance_"))
    temp_output = temp_dir / f"enhanced{destination.suffix or '.mp4'}"
    scale_filter = (
        f"hqdn3d=1.4:1.4:6:6,"
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "format=yuv420p"
    )
    video_encoder = _video_encoder()
    cmd = [
        shutil.which(ffmpeg_bin) or ffmpeg_bin,
        "-y",
        "-i", str(source),
        "-vf", scale_filter,
        "-c:v", *video_encoder,
        "-c:a", "aac",
        "-b:a", "160k",
        str(temp_output),
    ]

    logger.info("Sora enhance start: {} -> {} [{}x{}]", source, destination, target_w, target_h)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(err[-500:] or f"ffmpeg exited with code {result.returncode}")
        temp_output.replace(destination)
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    logger.info("Sora enhance done: {} ({:.1f} MB)", destination, destination.stat().st_size / 1_048_576)
    return destination
