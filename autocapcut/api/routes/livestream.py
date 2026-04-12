"""Livestream route — multi-channel 24/7 YouTube RTMP streaming via FFmpeg."""
from __future__ import annotations

import asyncio
import random
import re
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# ─── In-memory state ─────────────────────────────────────────────────────────

_channels: Dict[str, dict] = {}
_tasks:    Dict[str, asyncio.Task] = {}
_logs:     Dict[str, List[str]] = {}
_stats:    Dict[str, dict] = {}

DEFAULT_RTMP = "rtmp://a.rtmp.youtube.com/live2"
LOG_BUFFER   = 500
VIDEO_EXTS   = {".mp4", ".mov", ".mkv", ".avi", ".flv"}

# ─── Models ───────────────────────────────────────────────────────────────────

class ChannelCreate(BaseModel):
    name: str
    stream_key: str
    video_dirs: List[str] = []
    play_mode: str = "loop"
    rtmp_url: str = DEFAULT_RTMP
    recursive: bool = False
    shuffle: bool = True
    bitrate: str = "6000k"
    fps: int = 30
    extra_ffmpeg_args: List[str] = []


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    stream_key: Optional[str] = None
    video_dirs: Optional[List[str]] = None
    play_mode: Optional[str] = None
    rtmp_url: Optional[str] = None
    recursive: Optional[bool] = None
    shuffle: Optional[bool] = None
    bitrate: Optional[str] = None
    fps: Optional[int] = None
    extra_ffmpeg_args: Optional[List[str]] = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _gather_videos(ch: dict) -> List[Path]:
    files: set[Path] = set()
    for d in ch.get("video_dirs", []):
        p = Path(d)
        if not p.exists():
            continue
        it = p.rglob("*") if ch.get("recursive") else p.glob("*")
        for f in it:
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                files.add(f)
    result = sorted(files)
    if ch.get("shuffle", True):
        random.shuffle(result)
    return result


def _add_log(ch_id: str, line: str) -> None:
    buf = _logs.setdefault(ch_id, [])
    ts = time.strftime("%H:%M:%S")
    buf.append(f"[{ts}] {line}")
    if len(buf) > LOG_BUFFER:
        del buf[:-LOG_BUFFER]


def _parse_kbps(bitrate: str) -> int:
    return int(bitrate.lower().rstrip("k"))


def _build_ffmpeg_cmd(ch: dict, video: Path) -> List[str]:
    bps = _parse_kbps(ch.get("bitrate", "6000k"))
    fps = int(ch.get("fps", 30))
    rtmp = f"{ch['rtmp_url'].rstrip('/')}/{ch['stream_key'].strip()}"
    cmd = [
        "ffmpeg", "-y",
        "-re",
        "-fflags", "+genpts+discardcorrupt",
        "-i", str(video),
        # Video
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-g", str(fps * 2),
        "-b:v", f"{bps}k",
        "-maxrate", f"{int(bps * 1.1)}k",
        "-bufsize", f"{bps * 2}k",
        "-threads", "0",
        # Audio
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        # Output
        "-map_metadata", "-1",
        "-f", "flv",
        rtmp,
    ]
    cmd.extend(ch.get("extra_ffmpeg_args", []))
    return cmd


# ─── Streaming coroutines ────────────────────────────────────────────────────

async def _run_ffmpeg(ch_id: str, video: Path, ch: dict) -> bool:
    for attempt in range(3):
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            cmd = _build_ffmpeg_cmd(ch, video)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )

            async def _read_stderr() -> None:
                assert proc is not None
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").rstrip()
                    # Parse FPS / bitrate for stats
                    if "fps=" in text:
                        m = re.search(r"fps=\s*([\d.]+)", text)
                        if m:
                            _stats[ch_id]["fps"] = float(m.group(1))
                    if "bitrate=" in text:
                        m = re.search(r"bitrate=\s*([\d.]+)kbits", text)
                        if m:
                            _stats[ch_id]["bitrate_kbps"] = float(m.group(1))
                    # Surface notable lines to log
                    lower = text.lower()
                    if any(k in lower for k in ("error", "fail", "opening", "output #", "stream mapping")):
                        _add_log(ch_id, text[:200])

            stderr_task = asyncio.create_task(_read_stderr())
            rc = await proc.wait()
            stderr_task.cancel()

            if rc == 0:
                return True
            _add_log(ch_id, f"✗ FFmpeg exit {rc} (attempt {attempt + 1}/3)")

        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        except Exception as exc:
            _add_log(ch_id, f"✗ {exc} (attempt {attempt + 1}/3)")
        finally:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

        if attempt < 2:
            await asyncio.sleep(2 ** attempt)

    return False


async def _stream_loop(ch_id: str) -> None:
    ch = _channels[ch_id]
    _stats[ch_id] = {
        "status": "streaming",
        "fps": 0.0,
        "bitrate_kbps": 0.0,
        "current_video": "",
        "started_at": time.time(),
        "total": 0,
        "ok": 0,
        "fail": 0,
    }
    _add_log(ch_id, f"▶ Starting: {ch['name']}")
    try:
        while True:
            videos = _gather_videos(ch)
            if not videos:
                _add_log(ch_id, "✗ No videos found in configured folders")
                _stats[ch_id]["status"] = "error"
                return

            for video in videos:
                if _stats[ch_id].get("status") != "streaming":
                    return
                _stats[ch_id]["current_video"] = video.name
                _stats[ch_id]["total"] += 1
                _add_log(ch_id, f"→ {video.name}")

                ok = await _run_ffmpeg(ch_id, video, ch)
                if ok:
                    _stats[ch_id]["ok"] += 1
                else:
                    _stats[ch_id]["fail"] += 1

                if _stats[ch_id].get("status") != "streaming":
                    return

            if ch.get("play_mode") == "play_once":
                _add_log(ch_id, "✓ Playlist done (play_once mode)")
                break

    except asyncio.CancelledError:
        _add_log(ch_id, "■ Stopped")
        raise
    except Exception as exc:
        _add_log(ch_id, f"✗ Fatal: {exc}")
    finally:
        _stats[ch_id]["status"] = "idle"
        _stats[ch_id]["current_video"] = ""


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/ffmpeg-check")
async def ffmpeg_check() -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            first_line = stdout.decode(errors="replace").splitlines()[0] if stdout else ""
            version = first_line.split("version ")[-1].split(" ")[0] if "version " in first_line else "unknown"
            return {"installed": True, "version": version}
    except (FileNotFoundError, asyncio.TimeoutError):
        pass
    return {"installed": False, "version": None}


@router.get("")
def list_channels() -> list:
    result = []
    for cid, ch in _channels.items():
        s = _stats.get(cid, {"status": "idle"})
        result.append({**ch, "id": cid, "status": s.get("status", "idle")})
    return result


@router.post("")
def create_channel(body: ChannelCreate) -> dict:
    cid = str(uuid.uuid4())[:8]
    _channels[cid] = body.model_dump()
    _channels[cid]["id"] = cid
    _logs[cid] = []
    _stats[cid] = {"status": "idle"}
    return {"id": cid, **_channels[cid], "status": "idle"}


@router.put("/{ch_id}")
def update_channel(ch_id: str, body: ChannelUpdate) -> dict:
    if ch_id not in _channels:
        raise HTTPException(404, "Channel not found")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    _channels[ch_id].update(patch)
    return _channels[ch_id]


@router.delete("/{ch_id}")
async def delete_channel(ch_id: str) -> dict:
    if ch_id not in _channels:
        raise HTTPException(404, "Channel not found")
    await _stop(ch_id)
    _channels.pop(ch_id, None)
    _logs.pop(ch_id, None)
    _stats.pop(ch_id, None)
    return {"ok": True}


@router.post("/{ch_id}/start")
async def start_channel(ch_id: str) -> dict:
    if ch_id not in _channels:
        raise HTTPException(404, "Channel not found")
    task = _tasks.get(ch_id)
    if task and not task.done():
        return {"ok": True, "message": "Already streaming"}
    loop = asyncio.get_event_loop()
    _tasks[ch_id] = loop.create_task(_stream_loop(ch_id))
    return {"ok": True}


@router.post("/{ch_id}/stop")
async def stop_channel(ch_id: str) -> dict:
    if ch_id not in _channels:
        raise HTTPException(404, "Channel not found")
    await _stop(ch_id)
    return {"ok": True}


@router.get("/{ch_id}/status")
def get_status(ch_id: str) -> dict:
    if ch_id not in _channels:
        raise HTTPException(404, "Channel not found")
    stats = dict(_stats.get(ch_id, {"status": "idle"}))
    logs = list(_logs.get(ch_id, []))[-50:]
    ch = dict(_channels[ch_id])
    ch.pop("stream_key", None)  # never leak key in status response
    return {"channel": ch, "stats": stats, "logs": logs}


@router.delete("/{ch_id}/logs")
def clear_logs(ch_id: str) -> dict:
    if ch_id not in _channels:
        raise HTTPException(404, "Channel not found")
    _logs[ch_id] = []
    return {"ok": True}


# ─── Internal helpers ─────────────────────────────────────────────────────────

async def _stop(ch_id: str) -> None:
    task = _tasks.get(ch_id)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if ch_id in _stats:
        _stats[ch_id]["status"] = "idle"
