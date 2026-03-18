"""
Cut Automate service ported to Python/FastAPI.

Pipeline steps:
  01. catstock   - cut videos into 10-second segments
  02. edit       - render with the bundled ct.wav background audio
  03. tachanh    - extract frames (1 frame/6s, NL-means + unsharp)
  04. tachmp3    - extract audio to MP3
  05. gop_le     - merge odd-numbered clips
  06. gop_chan   - merge even-numbered clips
  07. reset      - delete intermediate files
  08. xoa_photo_le   - delete odd-numbered images
  09. xoa_photo_chan  - delete even-numbered images
  10. gop_photo_random - merge images into a random video
  11. gop_video_stock_random - merge random stock videos
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

# ─── helpers ──────────────────────────────────────────────────────────────────

# Background track bundled with the original tool (optional — skip if missing)
_SCRIPT_DIR = Path(__file__).parent
_BG_WAV = _SCRIPT_DIR.parent.parent / "bg" / "ct.wav"  # may not exist


def _ffmpeg(*args: str, log: Callable[[str], None] | None = None) -> None:
    """Run ffmpeg and stream stdout/stderr line-by-line to log callback."""
    cmd = ["ffmpeg", "-y", *args]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert proc.stdout
    for line in proc.stdout:
        stripped = line.rstrip()
        if stripped and log:
            log(stripped)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with code {proc.returncode}")


def _check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


# ─── 01 · catstock ────────────────────────────────────────────────────────────

def catstock(
    input_dir: str,
    output_dir: str,
    segment_time: int = 10,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Cut all videos in input_dir into segments of segment_time seconds."""
    inp = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    files = [f for f in inp.iterdir() if f.is_file() and f.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".m4v"}]
    if not files:
        raise ValueError(f"No video files found in: {input_dir}")

    results: list[str] = []
    for f in sorted(files):
        if log:
            log(f"Cutting: {f.name}")
        _ffmpeg(
            "-i", str(f),
            "-map", "0",
            "-f", "segment",
            "-c", "copy",
            "-reset_timestamps", "1",
            "-segment_time", str(segment_time),
            "-break_non_keyframes", "0",
            str(out / "%04d.mp4"),
            log=log,
        )
    results = [str(p) for p in sorted(out.glob("*.mp4"))]
    if log:
        log(f"Stock cut complete! {len(results)} segment(s)")
    return results


# ─── 02 · edit ────────────────────────────────────────────────────────────────

def edit_video(
    input_dir: str,
    output_dir: str,
    bg_wav: str | None = None,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Render videos with overlay crop, EQ and optional background track."""
    inp = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Resolve bg wav
    wav = Path(bg_wav) if bg_wav else _BG_WAV
    has_bg = wav.exists()

    files = [f for f in inp.iterdir() if f.is_file() and f.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".m4v"}]
    if not files:
        raise ValueError(f"No video files found in: {input_dir}")

    results: list[str] = []
    for f in sorted(files):
        stem = f.stem
        out_file = out / f"{stem}.mp4"
        if log:
            log(f"Rendering: {f.name}")

        if has_bg:
            filter_complex = (
                "[0:v]scale=1280:720[v1];"
                "[0:v]scale=1280:720[v2];"
                "[v2]crop=300:169:428:150,scale=1280:720[im];"
                "[v1][im]overlay=shortest=1:enable='lt(mod(t,7),5)*gte(t,11)':x=1:y=0[fn];"
                "[fn]boxblur=0,eq=brightness=+0.08:saturation=1.1;"
                "[0:a]atempo=1,volume=enable='lt(mod(t,99),0.7)*gte(t,24)':volume=0.2,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=mono,"
                "asetrate=10/10*44100,atempo=10/10,lowpass=f=1650,highpass=f=80,volume=4,bass=g=-18[a1];"
                f"amovie={wav}:loop=99999999,volume=1[a2];"
                "[a1][a2]amix=duration=shortest"
            )
        else:
            filter_complex = (
                "[0:v]scale=1280:720[v1];"
                "[0:v]scale=1280:720[v2];"
                "[v2]crop=300:169:428:150,scale=1280:720[im];"
                "[v1][im]overlay=shortest=1:enable='lt(mod(t,7),5)*gte(t,11)':x=1:y=0[fn];"
                "[fn]boxblur=0,eq=brightness=+0.08:saturation=1.1"
            )

        _ffmpeg(
            "-ss", "20",
            "-i", str(f),
            "-filter_complex", filter_complex,
            "-vcodec", "libx264",
            "-pix_fmt", "yuv420p",
            "-g", "60",
            "-shortest",
            "-acodec", "aac",
            "-b:a", "64k",
            "-ar", "44100",
            "-metadata", "album_artist=",
            "-metadata", "album=",
            "-metadata", "date=",
            "-metadata", "track=",
            "-metadata", "genre=",
            "-metadata", "language=eng",
            "-threads", "0",
            "-preset", "veryfast",
            str(out_file),
            log=log,
        )
        results.append(str(out_file))

    if log:
        log(f"Render complete! {len(results)} file(s)")
    return results


# ─── 03 · tachanh ─────────────────────────────────────────────────────────────

def tachanh(
    input_dir: str,
    output_dir: str,
    fps_fraction: str = "1/6",
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Extract frames from videos at fps_fraction rate with NL-means + unsharp."""
    inp = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    files = [f for f in inp.iterdir() if f.is_file() and f.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".m4v"}]
    if not files:
        raise ValueError(f"No video files found in: {input_dir}")

    for f in sorted(files):
        if log:
            log(f"Extracting frames from: {f.name}")
        _ffmpeg(
            "-i", str(f),
            "-vf", f"fps={fps_fraction},nlmeans=s=11:p=3:pc=6,unsharp=3:3:1.0",
            "-q:v", "0",
            "-threads", "0",
            str(out / "%04d.jpg"),
            log=log,
        )

    results = [str(p) for p in sorted(out.glob("*.jpg"))]
    if log:
        log(f"Frame extraction complete! {len(results)} frame(s)")
    return results


# ─── 04 · tachmp3 ─────────────────────────────────────────────────────────────

def tachmp3(
    input_dir: str,
    output_dir: str,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Extract audio tracks from videos to MP3."""
    inp = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    files = [f for f in inp.iterdir() if f.is_file() and f.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".m4v"}]
    if not files:
        raise ValueError(f"No video files found in: {input_dir}")

    results: list[str] = []
    for f in sorted(files):
        out_file = out / f"{f.stem}.mp3"
        if log:
            log(f"Extracting audio: {f.name}")
        _ffmpeg(
            "-i", str(f),
            "-vn",
            "-c:a", "libmp3lame",
            "-q:a", "0",
            str(out_file),
            log=log,
        )
        results.append(str(out_file))

    if log:
        log(f"MP3 extraction complete! {len(results)} file(s)")
    return results


# ─── 05 · gop_le ──────────────────────────────────────────────────────────────

def gop_le(
    input_dir: str,
    output_dir: str,
    output_name: str = "video_gop_le.mp4",
    log: Callable[[str], None] | None = None,
) -> str:
    """Concatenate odd-numbered clips (1, 3, 5, …)."""
    return _gop_parity(input_dir, output_dir, output_name, odd=True, log=log)


# ─── 06 · gop_chan ────────────────────────────────────────────────────────────

def gop_chan(
    input_dir: str,
    output_dir: str,
    output_name: str = "video_gop_chan.mp4",
    log: Callable[[str], None] | None = None,
) -> str:
    """Concatenate even-numbered clips (2, 4, 6, …)."""
    return _gop_parity(input_dir, output_dir, output_name, odd=False, log=log)


def _gop_parity(
    input_dir: str,
    output_dir: str,
    output_name: str,
    odd: bool,
    log: Callable[[str], None] | None = None,
) -> str:
    inp = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_file = out / output_name

    clips = sorted(
        [p for p in inp.glob("*.mp4") if p.stem.isdigit()],
        key=lambda p: int(p.stem),
    )
    selected = [p for p in clips if (int(p.stem) % 2 == 1) == odd]
    if not selected:
        raise ValueError(f"No {'odd' if odd else 'even'}-numbered clips available to merge.")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        for p in selected:
            fh.write(f"file '{p.resolve()}'\n")
        concat_list = fh.name

    try:
        if log:
            log(f"Merging {len(selected)} {'odd' if odd else 'even'}-numbered clip(s)...")
        _ffmpeg(
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            str(out_file),
            log=log,
        )
    finally:
        os.unlink(concat_list)

    if log:
        log(f"Done! File saved to: {out_file}")
    return str(out_file)


# ─── 07 · reset ───────────────────────────────────────────────────────────────

def reset(
    project_dir: str,
    dirs: list[str] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Delete all files in intermediate output directories."""
    root = Path(project_dir)
    target_dirs = dirs or ["01.catstock", "02.Edit", "03.tachanh", "04.tachmp3", "05.gop_le", "06.gop_chan"]
    deleted = []
    for d in target_dirs:
        p = root / d
        if p.is_dir():
            for f in p.iterdir():
                if f.is_file():
                    f.unlink()
                    deleted.append(str(f))
            if log:
                log(f"Deleted files in {d}")
        else:
            if log:
                log(f"Skipped {d} (does not exist)")
    if log:
        log(f"Reset complete! Deleted {len(deleted)} file(s).")
    return deleted


# ─── 08 · xoa_photo_le ────────────────────────────────────────────────────────

def xoa_photo_le(
    image_dir: str,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Delete odd-numbered jpg frames (1, 3, 5, …)."""
    return _xoa_photo_parity(image_dir, odd=True, dry_run=dry_run, log=log)


# ─── 09 · xoa_photo_chan ──────────────────────────────────────────────────────

def xoa_photo_chan(
    image_dir: str,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Delete even-numbered jpg frames (2, 4, 6, …)."""
    return _xoa_photo_parity(image_dir, odd=False, dry_run=dry_run, log=log)


def _xoa_photo_parity(
    image_dir: str,
    odd: bool,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    p = Path(image_dir)
    affected: list[str] = []
    for img in sorted(p.glob("*.jpg")):
        if not img.stem.isdigit():
            continue
        num = int(img.stem)
        if (num % 2 == 1) == odd:
            if dry_run:
                if log:
                    log(f"[PREVIEW] Would delete: {img.name}")
            else:
                img.unlink()
                if log:
                    log(f"Deleted: {img.name}")
            affected.append(str(img))
    if log:
        log(f"Done. {'Previewed' if dry_run else 'Deleted'} {len(affected)} {'odd' if odd else 'even'}-numbered image(s).")
    return affected


# ─── 10 · gop_photo_random ────────────────────────────────────────────────────

def gop_photo_random(
    image_dir: str,
    output_dir: str,
    fps: int = 1,
    output_name: str = "video_random.mp4",
    log: Callable[[str], None] | None = None,
) -> str:
    """Shuffle jpg images and concatenate into a slideshow video."""
    imgs = sorted(Path(image_dir).glob("*.jpg"))
    if not imgs:
        raise ValueError(f"No .jpg images found in: {image_dir}")

    random.shuffle(imgs)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_file = out / output_name

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        for p in imgs:
            fh.write(f"file '{p.resolve()}'\n")
        list_file = fh.name

    try:
        if log:
            log(f"Merging {len(imgs)} images into a random video...")
        _ffmpeg(
            "-r", str(fps),
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-vf", "scale=1920:1080,format=yuv420p",
            str(out_file),
            log=log,
        )
    finally:
        os.unlink(list_file)

    if log:
        log(f"Done! Video saved to: {out_file}")
    return str(out_file)


# ─── 11 · gop_video_stock_random ─────────────────────────────────────────────

def gop_video_stock_random(
    input_dir: str,
    output_dir: str,
    output_name: str = "gop_stock_random.mp4",
    log: Callable[[str], None] | None = None,
) -> str:
    """Shuffle stock video clips and concatenate with re-encode."""
    videos = list(Path(input_dir).glob("*.mp4"))
    if not videos:
        raise ValueError(f"No .mp4 videos found in: {input_dir}")

    random.shuffle(videos)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_file = out / output_name

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        for p in videos:
            fh.write(f"file '{p.resolve()}'\n")
        list_file = fh.name

    try:
        if log:
            log(f"Merging {len(videos)} random stock video(s)...")
        _ffmpeg(
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c:v", "libx264",
            "-crf", "20",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-b:a", "192k",
            str(out_file),
            log=log,
        )
    finally:
        os.unlink(list_file)

    if log:
        log(f"Done! File saved to: {out_file}")
    return str(out_file)


# ─── project structure ────────────────────────────────────────────────────────

REQUIRED_PROJECT_DIRS = [
    "00.videogoc",
    "bg",
    "01.catstock",
    "02.Edit",
    "03.tachanh",
    "04.tachmp3",
    "05.gop_le",
    "06.gop_chan",
    "10.gop_photo_random",
    "11.gop_video_stock_random",
]


def scan_project(project_dir: str) -> dict:
    """Scan a project folder and return which required subdirs are present/missing."""
    root = Path(project_dir)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Directory does not exist: {project_dir}")

    present: list[str] = []
    missing: list[str] = []
    for name in REQUIRED_PROJECT_DIRS:
        if (root / name).is_dir():
            present.append(name)
        else:
            missing.append(name)

    return {
        "project_dir": str(root.resolve()),
        "required": REQUIRED_PROJECT_DIRS,
        "present": present,
        "missing": missing,
        "is_complete": len(missing) == 0,
    }


def init_project(project_dir: str, create_if_missing: bool = True) -> dict:
    """Create the full standard folder structure inside project_dir.
    
    If create_if_missing=True (default), also creates project_dir itself.
    """
    root = Path(project_dir)
    if create_if_missing:
        root.mkdir(parents=True, exist_ok=True)
    elif not root.exists():
        raise ValueError(f"Directory does not exist: {project_dir}")

    created: list[str] = []
    already_existed: list[str] = []
    for name in REQUIRED_PROJECT_DIRS:
        sub = root / name
        if sub.exists():
            already_existed.append(name)
        else:
            sub.mkdir(parents=True, exist_ok=True)
            created.append(name)

    return {
        "project_dir": str(root.resolve()),
        "created": created,
        "already_existed": already_existed,
        "required": REQUIRED_PROJECT_DIRS,
    }


# ─── status ───────────────────────────────────────────────────────────────────

def get_status() -> dict:
    ok = _check_ffmpeg()
    return {
        "ffmpeg_available": ok,
        "ready": ok,
    }
