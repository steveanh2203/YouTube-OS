"""Sora Gen service — API key management, video download, auto-rename."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import httpx
from loguru import logger

# API key lưu tại ~/.autocapcut/sora_key.txt
_KEY_PATH = Path.home() / ".autocapcut" / "sora_key.txt"


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


# ---------------------------------------------------------------------------
# Download + Auto-rename
# ---------------------------------------------------------------------------

def next_video_filename(folder: Path) -> str:
    """
    Scan folder, tìm pattern video\\d{3}.mp4, trả về tên tiếp theo.
    Ví dụ: video001.mp4, video002.mp4, ...
    """
    existing = set()
    pattern = re.compile(r"^video(\d{3})\.mp4$", re.IGNORECASE)
    for f in folder.iterdir():
        m = pattern.match(f.name)
        if m:
            existing.add(int(m.group(1)))

    n = 1
    while n in existing:
        n += 1
    return f"video{n:03d}.mp4"


async def download_video(url: str, dest_folder: Path) -> Path:
    """
    Download MP4 từ URL về dest_folder.
    Tự đặt tên video001.mp4, video002.mp4, ...
    Trả về Path của file đã lưu.
    """
    dest_folder.mkdir(parents=True, exist_ok=True)
    filename = next_video_filename(dest_folder)
    dest = dest_folder / filename

    logger.info("Downloading Sora video → {}", dest)

    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=1024 * 256):
                    f.write(chunk)

    logger.info("Sora video saved: {} ({:.1f} MB)", dest, dest.stat().st_size / 1_048_576)
    return dest
