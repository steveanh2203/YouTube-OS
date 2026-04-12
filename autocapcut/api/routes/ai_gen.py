"""AI Image Generation — Whisk (Google) & Google Flow backends.

Google Flow logic uses WhiskForge-Pro's proven generator directly.
Whisk logic uses local implementation.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import tempfile
import threading
import time
import uuid
from typing import Optional
from urllib.parse import quote

from curl_cffi import requests as cffi_requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from autocapcut.services.upscale_engine import upscale_image_file

# WhiskForge-Pro proven generation engine
from autocapcut.services.wf_generator import (
    build_output_filename,
    generate_image_with_flow_batch as wf_generate_flow,
    generate_image_with_recipe as wf_generate_recipe,
    get_bearer_token_from_session as wf_get_bearer,
    build_runtime_session as wf_build_session,
    create_workflow as wf_create_workflow,
    generate_image_from_workflow as wf_generate_from_workflow,
)

router = APIRouter()

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
OUTPUT_CONFLICT_MODES = {"overwrite", "keep_both", "replace_all"}
OUTPUT_IMAGE_NAME_RE = re.compile(r"^image_(\d+)(?:\.[^.]+)?$", re.IGNORECASE)
OUTPUT_FOLDER_STATE_TTL_SEC = 15 * 60

_output_folder_guard = threading.Lock()
_output_folder_locks: dict[str, threading.Lock] = {}
_output_folder_state: dict[str, dict[str, float | int]] = {}

# ── In-memory store for extension-pushed Flow tokens ─────────────────────────
# Extension POSTs to /api/flow-tokens; frontend polls /api/flow-tokens/latest
_flow_token_store: dict = {
    "bearerToken": "",
    "projectId": "",
    "sessionCookie": "",
    "autoStart": False,
    "timestamp": 0,   # Unix ms — frontend compares to detect new pushes
}


class FlowTokenPush(BaseModel):
    bearerToken: str = ""
    projectId: str = ""
    sessionCookie: str = ""
    autoStart: bool = False


@router.post("/flow-tokens")
async def receive_flow_token(payload: FlowTokenPush):
    """Called by the Google Flow Helper Chrome extension."""
    _flow_token_store.update({
        "bearerToken":   payload.bearerToken.strip(),
        "projectId":     payload.projectId.strip(),
        "sessionCookie": payload.sessionCookie.strip(),
        "autoStart":     payload.autoStart,
        "timestamp":     int(time.time() * 1000),
    })
    return {"ok": True}


@router.get("/flow-tokens/latest")
async def get_latest_flow_token():
    """Frontend polls this to detect token pushes from the extension."""
    return _flow_token_store


@router.post("/output-folder/inspect")
async def inspect_output_folder(req: OutputFolderInspectRequest):
    folder = _normalize_output_folder(req.folder)
    image_count, managed_image_count, sample_names, video_count, video_sample_names = _describe_output_folder(folder)
    return {
        "folder": folder,
        "exists": os.path.isdir(folder),
        "image_count": image_count,
        "managed_image_count": managed_image_count,
        "sample_names": sample_names,
        "video_count": video_count,
        "video_sample_names": video_sample_names,
    }


@router.post("/output-folder/init")
async def init_output_folder(req: OutputFolderInitRequest):
    start_index, image_count_before, removed_image_count = _reserve_output_range(
        req.folder,
        req.conflict_mode,
        req.expected_count,
    )
    return {
        "folder": _normalize_output_folder(req.folder),
        "conflict_mode": str(req.conflict_mode or "keep_both").strip().lower(),
        "start_index": start_index,
        "image_count_before": image_count_before,
        "removed_image_count": removed_image_count,
    }

# ── Aspect ratio mapping ──────────────────────────────────────────────────────
ASPECT_RATIO_MAP = {
    "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "1:1":  "IMAGE_ASPECT_RATIO_SQUARE",
    "4:3":  "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "3:4":  "IMAGE_ASPECT_RATIO_PORTRAIT",
}

# ── Style prefixes ────────────────────────────────────────────────────────────
STYLE_PREFIXES: dict[str, str] = {
    "Auto": "",
    "Super Realistic": "super realistic photo, ultra detailed, 8k, ",
    "Cartoon": "cartoon style, vivid colors, flat illustration, ",
    "Sketch": "pencil sketch, black and white, detailed lines, ",
    "Anime": "anime style, vibrant, cel-shading, ",
    "3D Render": "3D render, octane render, studio lighting, photorealistic, ",
    "Oil Painting": "oil painting, classical art style, rich texture, ",
    "Watercolor": "watercolor painting, soft colors, flowing, ",
    "Cyberpunk": "cyberpunk style, neon lights, futuristic, dark city, ",
    "Vintage": "vintage photo, retro style, film grain, faded colors, ",
    "Minimalist": "minimalist design, clean, simple, flat, ",
    "Fantasy": "fantasy art, magical, epic, dramatic lighting, ",
    "Pop Art": "pop art style, bold colors, Warhol-inspired, ",
    "Isometric": "isometric illustration, flat design, vector art, ",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

REFERENCE_UPLOAD_URL = "https://labs.google/fx/api/trpc/backbone.uploadImage"
RECIPE_URLS = [
    "https://aisandbox-pa.googleapis.com/v1/whisk:runImageRecipe",
    "https://aisandbox-pa.googleapis.com/v1:runImageRecipe",
]

# ── Pydantic models ───────────────────────────────────────────────────────────
class WhiskGenerateRequest(BaseModel):
    backend: str          # "whisk" | "google_flow"
    cookie: str           # Whisk: session cookie. Flow: bearer token (ya29.xxx)
    prompt: str
    aspect_ratio: str = "16:9"
    style: str = "Auto"
    seed: Optional[int] = None
    reference_media_ids: list[str] = []   # pre-uploaded media IDs
    # Google Flow only
    flow_project_id: str = ""
    flow_model_name: str = "BANANA_PRO_2"
    flow_session_cookie: str = ""         # Flow: separate session cookie for Cookie: header
    # Output folder — auto-save generated images to this path
    output_folder: str = ""
    output_index: Optional[int] = None


class UploadReferenceRequest(BaseModel):
    cookie: str
    image_data: str   # base64 data URL  e.g. "data:image/jpeg;base64,..."
    backend: str = "whisk"
    flow_session_cookie: str = ""


class OutputFolderInspectRequest(BaseModel):
    folder: str


class OutputFolderInitRequest(BaseModel):
    folder: str
    conflict_mode: str = "keep_both"
    expected_count: int = 1


# ── Helpers ───────────────────────────────────────────────────────────────────
def _normalize_output_folder(folder: str) -> str:
    value = os.path.abspath(os.path.expanduser(folder.strip()))
    if not value:
        raise HTTPException(status_code=400, detail="Output folder is required.")
    if os.path.exists(value) and not os.path.isdir(value):
        raise HTTPException(status_code=400, detail="Output path must be a folder.")
    return value


def _is_image_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def _is_video_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def _iter_output_images(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    images: list[str] = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file() and _is_image_path(entry.path):
                images.append(entry.path)
    return images


def _iter_output_videos(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    videos: list[str] = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file() and _is_video_path(entry.path):
                videos.append(entry.path)
    return videos


def _managed_output_index(path: str) -> int | None:
    match = OUTPUT_IMAGE_NAME_RE.match(os.path.basename(path))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _describe_output_folder(folder: str) -> tuple[int, int, list[str], int, list[str]]:
    image_paths = sorted(_iter_output_images(folder), key=lambda item: os.path.basename(item).lower())
    video_paths = sorted(_iter_output_videos(folder), key=lambda item: os.path.basename(item).lower())
    managed_count = sum(1 for path in image_paths if _managed_output_index(path) is not None)
    sample_names = [os.path.basename(path) for path in image_paths[:3]]
    video_sample_names = [os.path.basename(path) for path in video_paths[:3]]
    return len(image_paths), managed_count, sample_names, len(video_paths), video_sample_names


def _current_output_start(image_paths: list[str]) -> int:
    managed_indices = [idx for path in image_paths if (idx := _managed_output_index(path)) is not None]
    return (max(managed_indices) + 1) if managed_indices else 1


def _get_output_folder_lock(folder: str) -> threading.Lock:
    with _output_folder_guard:
        lock = _output_folder_locks.get(folder)
        if lock is None:
            lock = threading.Lock()
            _output_folder_locks[folder] = lock
        return lock


def _cleanup_output_folder_state(now: float) -> None:
    stale_folders = [
        folder
        for folder, state in _output_folder_state.items()
        if now - float(state.get("updated_at", 0.0)) > OUTPUT_FOLDER_STATE_TTL_SEC
    ]
    for folder in stale_folders:
        _output_folder_state.pop(folder, None)


def _reserve_output_range(folder: str, conflict_mode: str, expected_count: int) -> tuple[int, int, int]:
    mode = str(conflict_mode or "keep_both").strip().lower()
    if mode not in OUTPUT_CONFLICT_MODES:
        raise HTTPException(status_code=400, detail="Invalid output conflict mode.")
    if expected_count < 1:
        raise HTTPException(status_code=400, detail="expected_count must be at least 1.")

    out_dir = _normalize_output_folder(folder)
    lock = _get_output_folder_lock(out_dir)
    with lock:
        now = time.time()
        _cleanup_output_folder_state(now)
        os.makedirs(out_dir, exist_ok=True)

        image_paths = _iter_output_images(out_dir)
        image_count_before = len(image_paths)
        removed_image_count = 0

        if mode == "replace_all":
            for image_path in image_paths:
                try:
                    os.remove(image_path)
                    removed_image_count += 1
                except OSError:
                    continue
            start_index = 1
            _output_folder_state[out_dir] = {
                "next_keep_both_index": expected_count + 1,
                "updated_at": now,
            }
            return start_index, image_count_before, removed_image_count

        if mode == "overwrite":
            _output_folder_state[out_dir] = {
                "next_keep_both_index": _current_output_start(image_paths),
                "updated_at": now,
            }
            return 1, image_count_before, 0

        current_start = _current_output_start(image_paths)
        reserved_start = max(
            current_start,
            int(_output_folder_state.get(out_dir, {}).get("next_keep_both_index", current_start)),
        )
        _output_folder_state[out_dir] = {
            "next_keep_both_index": reserved_start + expected_count,
            "updated_at": now,
        }
        return reserved_start, image_count_before, 0


def _save_output_image(image_bytes: bytes, output_folder: str, output_index: Optional[int], extension: str) -> str:
    target_index = int(output_index) if output_index is not None else _reserve_output_range(output_folder, "keep_both", 1)[0]
    out_dir = _normalize_output_folder(output_folder)
    os.makedirs(out_dir, exist_ok=True)
    save_path = build_output_filename(out_dir, target_index, extension)
    with open(save_path, "wb") as handle:
        handle.write(image_bytes)
    return save_path


def _resolve_seed(seed: Optional[int]) -> int:
    if seed is not None and seed >= 0:
        return seed
    return random.randint(100000, 999999)


def _looks_like_bearer(value: str) -> bool:
    """Faithfully ported from Whisk Forge _looks_like_bearer_token."""
    v = value.strip()
    if not v:
        return False
    # Cookie strings always contain = (key=value pairs)
    if "=" in v:
        return False
    if v.startswith("ya29."):
        return True
    if v.startswith("eyJ"):
        return True
    # Google Flow internal tokens (wv_xxx format — no dots, no ya29. prefix)
    if v.startswith("wv_"):
        return True
    # reCAPTCHA tokens — explicitly reject
    if v.startswith("0cAF") or v.startswith("0cAf"):
        return False
    # Generic bearer: long, no semicolons, no equals, has dot
    if len(v) > 50 and ";" not in v and "=" not in v and "." in v:
        return True
    return False


def _build_session(cookie: str, referer: str, session_cookie: str = "", mode: str = "") -> cffi_requests.Session:
    """Build curl_cffi session with full browser headers (matches Whisk Forge).

    cookie         — Whisk: session cookie string | Flow: bearer token (ya29. or wv_xxx)
    session_cookie — Flow only: explicit session cookie string (Cookie: header).
                     When provided, always set as Cookie: regardless of `cookie` type.
    mode           — "google_flow" or "whisk". Affects which tokens are excluded from Cookie.

    Whisk Forge cookie-header rules (build_runtime_session lines 249-255):
      - Flow mode + ya29. token  → NOT in Cookie header (Authorization only)
      - Flow mode + wv_xxx token → IS in Cookie header (AND Authorization)
      - Whisk mode               → cookie goes in Cookie header unless it's a bearer
    """
    session = cffi_requests.Session(impersonate="chrome131")
    headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://labs.google",
        "priority": "u=1, i",
        "referer": referer,
        "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": USER_AGENT,
    }
    # Set Cookie: header — prefer explicit session_cookie, fall back based on mode
    sc = session_cookie.strip()
    cv = cookie.strip()
    if sc:
        # Explicit session cookie always takes priority
        effective_cookie = sc
    elif mode == "google_flow":
        # Flow mode: only ya29. tokens are excluded from Cookie header.
        # wv_xxx tokens go in BOTH Authorization and Cookie headers (Whisk Forge behaviour).
        effective_cookie = "" if cv.startswith("ya29.") else cv
    else:
        # Whisk mode: bearer tokens do not belong in Cookie header
        effective_cookie = "" if _looks_like_bearer(cv) else cv
    if effective_cookie:
        headers["cookie"] = effective_cookie
    session.headers.update(headers)
    return session


def _get_bearer_token(session: cffi_requests.Session, cookie: str, mode: str, session_cookie: str = "") -> str:
    """Exchange session cookie → bearer token via labs.google session endpoint.

    Priority (matches Whisk Forge):
    1. Flow mode + session_cookie provided (real cookie, not bearer):
       → exchange session_cookie for a fresh bearer via /api/auth/session
         (handles expired ya29. tokens automatically)
    2. Flow mode + cookie looks like bearer (ya29., wv_, eyJ…):
       → use it directly (no exchange needed)
    3. Anything else:
       → exchange cookie via /api/auth/session
    """
    flow_referer = "https://labs.google/fx/tools/flow"
    whisk_referer = "https://labs.google/fx/tools/whisk"
    referer = flow_referer if mode == "google_flow" else whisk_referer

    # ── Step 1: Flow + real session cookie → exchange for fresh bearer ──────
    sc = session_cookie.strip()
    if mode == "google_flow" and sc and not _looks_like_bearer(sc):
        resp = session.get(
            "https://labs.google/fx/api/auth/session",
            headers={"referer": flow_referer, "cookie": sc},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("access_token") or data.get("accessToken", "")
            if token:
                return token
        # Session exchange failed — fall through to try bearer directly

    # ── Step 2: Flow + cookie already looks like bearer → use as-is ─────────
    if mode == "google_flow" and _looks_like_bearer(cookie):
        return cookie.strip()

    # ── Step 3: Exchange cookie via session endpoint ─────────────────────────
    resp = session.get(
        "https://labs.google/fx/api/auth/session",
        headers={"referer": referer},
        timeout=30,
    )
    if resp.status_code == 200:
        data = resp.json()
        token = data.get("access_token") or data.get("accessToken", "")
        if token:
            return token
    raise HTTPException(
        status_code=401,
        detail=f"Auth failed (HTTP {resp.status_code}). Cookie / token may be expired.",
    )


def _parse_uploaded_media_id(data: object) -> str:
    """
    Parse mediaGenerationId from tRPC response.
    Faithfully ported from Whisk Forge _parse_uploaded_media_id.
    """
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            mid = (
                item.get("result", {})
                .get("data", {})
                .get("json", {})
                .get("mediaGenerationId")
            )
            if mid:
                return mid
            return (
                item.get("result", {})
                .get("data", {})
                .get("json", {})
                .get("result", {})
                .get("uploadMediaGenerationId", "")
            ) or ""
    if isinstance(data, dict):
        mid = (
            data.get("result", {})
            .get("data", {})
            .get("json", {})
            .get("mediaGenerationId")
        )
        if mid:
            return mid
        return (
            data.get("result", {})
            .get("data", {})
            .get("json", {})
            .get("result", {})
            .get("uploadMediaGenerationId", "")
        ) or ""
    return ""


def _first_generated_image(data: dict) -> dict:
    panels = data.get("imagePanels", [])
    if panels and isinstance(panels[0], dict):
        imgs = panels[0].get("generatedImages", [])
        if imgs and isinstance(imgs[0], dict):
            return imgs[0]
    return {}


def _raw_cookie(session: cffi_requests.Session) -> str:
    """Return raw cookie string from session headers if it's a real cookie (not bearer)."""
    raw = session.headers.get("cookie", "")
    if raw and "=" in raw and not _looks_like_bearer(raw):
        return raw
    return ""


# ── Upload reference image ────────────────────────────────────────────────────
def _upload_reference(session: cffi_requests.Session, image_data: str) -> str:
    payload = {
        "json": {
            "clientContext": {
                "workflowId": str(uuid.uuid4()),
                "sessionId": str(int(time.time() * 1000)),
            },
            "uploadMediaInput": {
                "mediaCategory": "MEDIA_CATEGORY_SUBJECT",
                "rawBytes": image_data,
                "caption": "",
            },
        }
    }
    for attempt in range(1, 4):
        try:
            resp = session.post(REFERENCE_UPLOAD_URL, json=payload, timeout=90)
        except Exception as exc:
            if attempt < 3:
                time.sleep(0.6 * attempt)
                continue
            raise HTTPException(status_code=502, detail=f"Reference upload error: {exc}")

        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                if attempt < 3:
                    time.sleep(0.6 * attempt)
                    continue
                raise HTTPException(status_code=502, detail="Reference upload: invalid JSON")

            media_id = _parse_uploaded_media_id(data)
            if media_id:
                return media_id
            if attempt < 3:
                time.sleep(0.6 * attempt)
                continue
            raise HTTPException(status_code=502, detail="No media_id returned from upload.")

        if attempt < 3:
            time.sleep(0.6 * attempt)
            continue
        raise HTTPException(
            status_code=502,
            detail=f"Reference upload failed (HTTP {resp.status_code})",
        )
    raise HTTPException(status_code=502, detail="Reference upload retry exhausted.")


# ── Whisk flow ────────────────────────────────────────────────────────────────
def _create_workflow(
    session: cffi_requests.Session,
    prompt: str,
    subject_media_ids: list[str] | None = None,
) -> str:
    """Create workflow. If subject_media_ids given, embed them as sources."""
    url = "https://labs.google/fx/api/trpc/media.createOrUpdateWorkflow"
    payload_json: dict = {"prompt": prompt, "experimentId": "imagefx"}
    if subject_media_ids:
        payload_json["sources"] = [
            {"category": "MEDIA_CATEGORY_SUBJECT", "mediaGenerationId": mid}
            for mid in subject_media_ids if mid
        ]
    resp = session.post(url, json={"json": payload_json}, timeout=60)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Workflow creation failed (HTTP {resp.status_code})",
        )
    data = resp.json()
    wid = (
        data.get("result", {})
        .get("data", {})
        .get("json", {})
        .get("result", {})
        .get("workflowId", "")
    )
    if not wid:
        raise HTTPException(status_code=502, detail="No workflowId returned.")
    return wid


def _generate_whisk(
    session: cffi_requests.Session,
    bearer: str,
    workflow_id: str,
    prompt: str,
    aspect_ratio: str,
    seed_value: int,
) -> bytes:
    """Generate via whisk:generateImage (no reference images)."""
    url = "https://aisandbox-pa.googleapis.com/v1/whisk:generateImage"
    payload = {
        "clientContext": {
            "workflowId": workflow_id,
            "tool": "BACKBONE",
            "sessionId": f";{int(time.time() * 1000)}",
        },
        "imageModelSettings": {
            "imageModel": "IMAGEN_3_5",
            "aspectRatio": ASPECT_RATIO_MAP.get(aspect_ratio, "IMAGE_ASPECT_RATIO_LANDSCAPE"),
        },
        "seed": seed_value,
        "prompt": prompt,
        "mediaCategory": "MEDIA_CATEGORY_BOARD",
    }
    headers = {
        "authorization": f"Bearer {bearer}",
        "content-type": "application/json",
        "origin": "https://labs.google",
        "priority": "u=1, i",
        "referer": "https://labs.google/fx/tools/whisk",
        "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "user-agent": USER_AGENT,
        "x-browser-channel": "stable",
        "x-browser-copyright": "Copyright 2026 Google LLC. All Rights reserved.",
        "x-browser-year": "2026",
        "x-client-data": "CKC1yQEIj7bJAQiltskBCKmdygEIyIfLAQiSocsBCIagzQEIlKTPAQ==",
    }
    raw = _raw_cookie(session)
    if raw:
        headers["cookie"] = raw

    resp = session.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Rate limit. Please wait and try again.")
    if resp.status_code == 400:
        raise HTTPException(status_code=400, detail="Prompt blocked by safety filter.")
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Auth failed — cookie may be expired.")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Generation failed (HTTP {resp.status_code})")

    data = resp.json()
    generated = _first_generated_image(data)
    encoded = generated.get("encodedImage", "")
    if not encoded:
        raise HTTPException(status_code=502, detail="No image returned by Whisk.")
    return base64.b64decode(encoded)


def _generate_whisk_recipe(
    session: cffi_requests.Session,
    bearer: str,
    prompt: str,
    aspect_ratio: str,
    seed_value: int,
    reference_media_ids: list[str],
) -> bytes:
    """
    Generate via whisk:runImageRecipe — used when reference images are present.
    Faithfully ported from Whisk Forge generate_image_with_recipe().
    """
    recipe_workflow_id = str(uuid.uuid4())
    payload = {
        "clientContext": {
            "workflowId": recipe_workflow_id,
            "tool": "BACKBONE",
            "sessionId": f"{int(time.time() * 1000)}",
        },
        "imageModelSettings": {
            "imageModel": "GEM_PIX",
            "aspectRatio": ASPECT_RATIO_MAP.get(aspect_ratio, "IMAGE_ASPECT_RATIO_LANDSCAPE"),
        },
        "seed": seed_value,
        "userInstruction": prompt,
        "recipeMediaInputs": [
            {
                "caption": "",
                "mediaInput": {
                    "mediaCategory": "MEDIA_CATEGORY_SUBJECT",
                    "mediaGenerationId": mid,
                },
            }
            for mid in reference_media_ids if mid
        ],
    }
    headers = {
        "authorization": f"Bearer {bearer}",
        "content-type": "application/json",
        "origin": "https://labs.google",
        "referer": "https://labs.google/fx/tools/whisk",
        "user-agent": USER_AGENT,
    }
    raw = _raw_cookie(session)
    if raw:
        headers["cookie"] = raw

    last_status = 502
    for idx, recipe_url in enumerate(RECIPE_URLS):
        try:
            resp = session.post(recipe_url, headers=headers, json=payload, timeout=120)
        except Exception as exc:
            if idx < len(RECIPE_URLS) - 1:
                continue
            raise HTTPException(status_code=502, detail=f"Recipe request error: {exc}")

        if resp.status_code == 200:
            data = resp.json()
            generated = _first_generated_image(data)
            encoded = generated.get("encodedImage", "")
            if encoded:
                return base64.b64decode(encoded)
            raise HTTPException(status_code=502, detail="No image returned by Whisk recipe.")
        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="Rate limit. Please wait and try again.")
        if resp.status_code == 400:
            raise HTTPException(status_code=400, detail="Prompt blocked by safety filter.")
        if resp.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Auth failed — cookie may be expired.")
        if resp.status_code == 404 and idx < len(RECIPE_URLS) - 1:
            continue
        last_status = resp.status_code
        if idx < len(RECIPE_URLS) - 1:
            continue
        raise HTTPException(status_code=502, detail=f"Recipe generation failed (HTTP {last_status})")

    raise HTTPException(status_code=502, detail="Recipe endpoint unavailable.")


# ── Google Flow ───────────────────────────────────────────────────────────────
def _download_flow_media_redirect(
    session: cffi_requests.Session,
    media_name: str,
    project_id: str,
) -> bytes | None:
    """
    Fallback: download Flow image via media.getMediaUrlRedirect.
    Ported from Whisk Forge _download_flow_media_via_redirect().
    """
    if not media_name:
        return None
    referer = (
        f"https://labs.google/fx/vi/tools/flow/project/{project_id}"
        if project_id
        else "https://labs.google/fx/tools/flow"
    )
    url = "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={}".format(
        quote(media_name, safe="")
    )
    img_headers = {
        "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://labs.google",
        "priority": "i",
        "referer": referer,
        "sec-fetch-dest": "image",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": USER_AGENT,
    }
    try:
        resp = session.get(url, headers=img_headers, timeout=60)
    except Exception:
        return None

    content_type = resp.headers.get("content-type", "").lower()
    if resp.status_code == 200 and content_type.startswith("image/") and resp.content:
        return bytes(resp.content)

    # Follow redirect manually
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location", "").strip()
        if location:
            try:
                r2 = session.get(location, headers={"accept": img_headers["accept"]}, timeout=60)
                if r2.status_code == 200 and r2.content:
                    return bytes(r2.content)
            except Exception:
                pass
    return None


def _collect_media_name_candidates(data: dict) -> list[str]:
    """Walk response JSON to collect possible media name strings."""
    keys = {"name", "mediaGenerationId", "media_generation_id", "mediaName", "outputMediaGenerationId"}
    found: list[str] = []
    seen: set[str] = set()

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k) in keys and isinstance(v, str):
                    t = v.strip()
                    if t and t not in seen:
                        seen.add(t)
                        found.append(t)
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)
    return found


def _generate_flow(
    session: cffi_requests.Session,
    bearer: str,
    project_id: str,
    model_name: str,
    prompt: str,
    aspect_ratio: str,
    seed_value: int,
    reference_media_ids: list[str] | None = None,
) -> bytes:
    """
    Generate via Google Flow batchGenerateImages.
    Includes model fallback (primary → NARWHAL) and redirect download fallback.
    Faithfully ported from Whisk Forge generate_image_with_flow_batch().
    """
    if not project_id:
        raise HTTPException(status_code=400, detail="Google Flow requires a Project ID.")

    image_inputs = [
        {"imageInputType": "IMAGE_INPUT_TYPE_REFERENCE", "name": mid}
        for mid in (reference_media_ids or []) if mid
    ]

    # Model candidates: try primary first, then NARWHAL as fallback
    primary = (model_name or "BANANA_PRO_2").strip()
    model_candidates = [primary]
    if "NARWHAL" not in primary:
        model_candidates.append("NARWHAL")

    url = f"https://aisandbox-pa.googleapis.com/v1/projects/{project_id}/flowMedia:batchGenerateImages"
    session_id = f";{int(time.time() * 1000)}"
    base_ctx = {"projectId": project_id, "tool": "PINHOLE", "sessionId": session_id}

    headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "authorization": f"Bearer {bearer}",
        "content-type": "text/plain;charset=UTF-8",
        "origin": "https://labs.google",
        "referer": "https://labs.google/",
        "user-agent": USER_AGENT,
    }
    raw = _raw_cookie(session)
    if raw:
        headers["cookie"] = raw

    last_error = "Flow generation failed."
    for model in model_candidates:
        payload = {
            "clientContext": base_ctx,
            "mediaGenerationContext": {"batchId": str(uuid.uuid4())},
            "useNewMedia": True,
            "requests": [
                {
                    "clientContext": base_ctx,
                    "imageModelName": model,
                    "imageAspectRatio": ASPECT_RATIO_MAP.get(aspect_ratio, "IMAGE_ASPECT_RATIO_LANDSCAPE"),
                    "structuredPrompt": {"parts": [{"text": prompt}]},
                    "seed": seed_value,
                    "imageInputs": image_inputs,
                }
            ],
        }
        body = json.dumps(payload, separators=(",", ":"))
        try:
            resp = session.post(url, headers=headers, data=body, timeout=180)
        except Exception as exc:
            last_error = str(exc)
            continue

        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="Rate limit. Please wait and try again.")
        if resp.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Auth failed — token may be expired.")
        if resp.status_code == 400:
            last_error = f"Flow 400 on model {model}"
            if model != model_candidates[-1]:
                continue
            raise HTTPException(status_code=400, detail="Prompt blocked or bad request.")
        if resp.status_code != 200:
            last_error = f"Flow HTTP {resp.status_code}"
            if model != model_candidates[-1]:
                continue
            raise HTTPException(status_code=502, detail=f"Flow generation failed (HTTP {resp.status_code})")

        try:
            data = resp.json()
        except Exception:
            data = {}

        generated = _first_generated_image(data)
        encoded = generated.get("encodedImage", "")
        if encoded:
            return base64.b64decode(encoded)

        # 200 OK but no inline image — try redirect-based download
        candidates = _collect_media_name_candidates(data)
        for media_name in candidates:
            img_bytes = _download_flow_media_redirect(session, media_name, project_id)
            if img_bytes:
                return img_bytes

        raise HTTPException(status_code=502, detail="No image returned by Google Flow.")

    raise HTTPException(status_code=502, detail=last_error)


# ── Upload reference image endpoint ──────────────────────────────────────────
@router.post("/upload-reference")
async def upload_reference_image(req: UploadReferenceRequest):
    """Upload a reference image to Google. Returns media_id + thumbnail."""
    if not req.cookie.strip():
        raise HTTPException(status_code=400, detail="Cookie is required.")
    if not req.image_data.strip():
        raise HTTPException(status_code=400, detail="Image data is required.")

    referer = (
        "https://labs.google/fx/tools/flow"
        if req.backend == "google_flow"
        else "https://labs.google/fx/tools/whisk"
    )

    loop = asyncio.get_running_loop()

    def _run():
        session = _build_session(req.cookie, referer, session_cookie=req.flow_session_cookie, mode=req.backend.strip().lower())
        media_id = _upload_reference(session, req.image_data)
        return media_id

    media_id = await loop.run_in_executor(None, _run)
    return {"media_id": media_id, "thumbnail": req.image_data}


# ── Main generate endpoint ────────────────────────────────────────────────────
@router.post("/generate")
async def generate_image(req: WhiskGenerateRequest):
    """
    Generate image via Whisk or Google Flow. Returns base64 data URL.

    Whisk without references : createWorkflow → generateImage        (IMAGEN_3_5)
    Whisk WITH references    : createWorkflow → runImageRecipe       (GEM_PIX)
    Google Flow              : batchGenerateImages with model fallback
    """
    if not req.cookie.strip():
        raise HTTPException(status_code=400, detail="Cookie / token is required.")
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required.")

    loop = asyncio.get_running_loop()

    def _run():
        style_prefix = STYLE_PREFIXES.get(req.style, "")
        final_prompt = f"{style_prefix}{req.prompt}".strip()
        seed_value = _resolve_seed(req.seed)
        mode = req.backend.strip().lower()
        has_references = bool(req.reference_media_ids)

        referer = (
            "https://labs.google/fx/tools/flow"
            if mode == "google_flow"
            else "https://labs.google/fx/tools/whisk"
        )
        session = _build_session(req.cookie, referer, session_cookie=req.flow_session_cookie, mode=mode)
        bearer = _get_bearer_token(session, req.cookie, mode, session_cookie=req.flow_session_cookie)

        if mode == "google_flow":
            # ── Use WhiskForge-Pro proven engine directly ────────────────────────
            flow_cookie = req.flow_session_cookie.strip() or req.cookie.strip()
            wf_session = wf_build_session(flow_cookie, generation_mode="google_flow")
            # Always set full session cookie in Cookie header for Flow
            if req.flow_session_cookie.strip():
                wf_session.headers["cookie"] = req.flow_session_cookie.strip()

            wf_bearer = wf_get_bearer(flow_cookie, lambda msg: None, generation_mode="google_flow")
            if not wf_bearer:
                wf_bearer = req.cookie.strip()

            runtime_opts = {
                "flow_project_id": req.flow_project_id,
                "flow_model_name": req.flow_model_name or "BANANA_PRO_2",
                "flow_session_cookie": req.flow_session_cookie,
            }
            _FLOW_FATAL = {"HTTP_401", "FLOW_PROJECT_ID_MISSING"}

            if has_references:
                # ── Flow + references: recipe endpoint (GEM_PIX) first ───────────
                # flow:runImageRecipe with recipeMediaInputs gives proper character
                # identity binding, unlike batchGenerateImages IMAGE_INPUT_TYPE_REFERENCE
                # which is only a loose style hint.
                ref_inputs = [{"media_id": mid} for mid in req.reference_media_ids if mid]
                img_bytes, err, _meta = wf_generate_recipe(
                    final_prompt, req.aspect_ratio, wf_bearer,
                    ref_inputs, lambda msg: None,
                    session=wf_session, seed=seed_value,
                    generation_mode="google_flow",
                )
                # Fallback: recipe failed → try batchGenerateImages with references
                if err and err not in _FLOW_FATAL and err not in ("RATE_LIMIT", "UNSAFE_PROMPT"):
                    img_bytes, err, _meta = wf_generate_flow(
                        final_prompt, req.aspect_ratio, wf_bearer,
                        lambda msg: None, session=wf_session,
                        seed=seed_value, runtime_options=runtime_opts,
                        reference_inputs=req.reference_media_ids or [],
                    )
            else:
                # ── Flow without references: batchGenerateImages ──────────────────
                img_bytes, err, _meta = wf_generate_flow(
                    final_prompt, req.aspect_ratio, wf_bearer,
                    lambda msg: None, session=wf_session,
                    seed=seed_value, runtime_options=runtime_opts,
                    reference_inputs=[],
                )
                # Fallback: batch failed → create_workflow + generateImage
                if err and err not in _FLOW_FATAL and err not in ("RATE_LIMIT", "UNSAFE_PROMPT"):
                    workflow_id, wf_err = wf_create_workflow(
                        wf_session, final_prompt, lambda msg: None,
                        generation_mode="google_flow",
                    )
                    if not wf_err and workflow_id:
                        img_bytes, err, _meta = wf_generate_from_workflow(
                            workflow_id, final_prompt, req.aspect_ratio,
                            wf_bearer, lambda msg: None, wf_session,
                            seed=seed_value, generation_mode="google_flow",
                        )
                    else:
                        err = wf_err or err

            if err:
                if err in ("HTTP_401", "HTTP_403"):
                    raise HTTPException(status_code=401, detail="Auth failed — token may be expired.")
                if err == "RATE_LIMIT":
                    raise HTTPException(status_code=429, detail="Rate limit. Please wait and try again.")
                if err == "UNSAFE_PROMPT":
                    raise HTTPException(status_code=400, detail="Prompt blocked by safety filter.")
                raise HTTPException(status_code=502, detail=f"Flow generation failed: {err}")
            if not img_bytes:
                raise HTTPException(status_code=502, detail="No image returned by Google Flow.")
            image_bytes = img_bytes

        elif has_references:
            # Whisk + reference images → recipe path (GEM_PIX model)
            # Pass references into workflow sources for context, then use recipe endpoint
            _create_workflow(session, final_prompt, req.reference_media_ids)
            image_bytes = _generate_whisk_recipe(
                session, bearer,
                final_prompt, req.aspect_ratio, seed_value,
                req.reference_media_ids,
            )
        else:
            # Whisk without references → standard path (IMAGEN_3_5)
            workflow_id = _create_workflow(session, final_prompt)
            image_bytes = _generate_whisk(
                session, bearer, workflow_id,
                final_prompt, req.aspect_ratio, seed_value,
            )

        b64 = base64.b64encode(image_bytes).decode()

        # ── Auto-save to output folder if specified ──────────────────────
        saved_path = ""
        out_dir = req.output_folder.strip()
        if out_dir:
            try:
                saved_path = _save_output_image(image_bytes, out_dir, req.output_index, ".jpg")
            except Exception:
                # Don't fail the generation if save fails
                pass

        return {
            "image": f"data:image/jpeg;base64,{b64}",
            "seed": seed_value,
            "backend": mode,
            "saved_path": saved_path,
        }

    return await loop.run_in_executor(None, _run)


# ── Upscale endpoint ──────────────────────────────────────────────────────────
class UpscaleRequest(BaseModel):
    image: str              # base64 data URL: "data:image/jpeg;base64,..."
    target_width: int = 0   # exact output width in pixels (e.g. 1920)
    target_height: int = 0  # exact output height in pixels (e.g. 1080)
    engine: str = "auto"    # "auto", "realesrgan", "opencv"
    # Output folder — overwrite the original saved file with upscaled version
    output_folder: str = ""
    output_index: Optional[int] = None
    prompt: str = ""        # used for filename when saving upscaled image
    seed: Optional[int] = None


@router.post("/upscale")
async def upscale_image(req: UpscaleRequest):
    """
    Upscale a base64 image to exact pixel dimensions using Real-ESRGAN (or OpenCV fallback).
    Accepts the same base64 data URL format returned by /generate.
    Returns upscaled image as base64 data URL.
    """
    if not req.image.strip():
        raise HTTPException(status_code=400, detail="Image data is required.")
    if req.target_width <= 0 or req.target_height <= 0:
        raise HTTPException(status_code=400, detail="target_width and target_height must be positive integers.")

    # Decode base64 → temp file
    try:
        header, _, b64data = req.image.partition(",")
        image_bytes = base64.b64decode(b64data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image data: {exc}")

    suffix = ".jpg"
    if "png" in header:
        suffix = ".png"
    elif "webp" in header:
        suffix = ".webp"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f_in:
        f_in.write(image_bytes)
        input_path = f_in.name

    output_path = os.path.splitext(input_path)[0] + f"_upscaled{suffix}"

    loop = asyncio.get_running_loop()

    def _run():
        options = {
            "engine": req.engine,
            "target_width": req.target_width,
            "target_height": req.target_height,
        }
        # Scale=4 gives Real-ESRGAN enough headroom for any FHD/2K/4K target.
        result = upscale_image_file(
            input_path,
            output_path,
            scale=4,
            engine=req.engine,
            options=options,
        )
        if not result.get("ok"):
            error = result.get("error", "Upscale failed")
            raise HTTPException(status_code=502, detail=error)

        with open(output_path, "rb") as f_out:
            upscaled_bytes = f_out.read()

        # ── Auto-save upscaled image to output folder if specified ───────
        saved_path = ""
        out_dir = req.output_folder.strip()
        if out_dir and upscaled_bytes:
            try:
                saved_path = _save_output_image(upscaled_bytes, out_dir, req.output_index, suffix)
            except Exception:
                pass

        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else f"image/{suffix.lstrip('.')}"
        b64 = base64.b64encode(upscaled_bytes).decode()
        return {
            "image": f"data:{mime};base64,{b64}",
            "width": result.get("width", 0),
            "height": result.get("height", 0),
            "engine": result.get("engine", req.engine),
            "elapsed_ms": result.get("elapsed_ms", 0),
            "saved_path": saved_path,
        }

    try:
        return await loop.run_in_executor(None, _run)
    finally:
        for p in (input_path, output_path):
            try:
                os.remove(p)
            except OSError:
                pass
