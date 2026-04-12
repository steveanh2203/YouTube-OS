import json
import time
import os
import shutil
import tempfile
import random
import queue
import re
import uuid
import hashlib
import mimetypes
from urllib.parse import quote
from datetime import datetime, timezone
# import requests  <-- REMOVED
from curl_cffi import requests # <-- ADDED
import base64
import threading as th
from concurrent.futures import ThreadPoolExecutor, as_completed

_upscale_loaded = False
try:
    from autocapcut.services.upscale_engine import get_default_upscale_options, upscale_image_file, pre_warm_realesrgan
    _upscale_loaded = True
except Exception:
    pass

if not _upscale_loaded:
    try:
        from upscale_engine import get_default_upscale_options, upscale_image_file, pre_warm_realesrgan
        _upscale_loaded = True
    except Exception:
        pass

if not _upscale_loaded:
    def pre_warm_realesrgan(options=None, log_callback=None):
        pass
    def get_default_upscale_options():
        return {
            "enabled": False,
            "scale": 2,
            "engine": "auto",
            "mode": "replace",
            "keep_raw": False,
            "bin_path": "",
            "model_name": "",
            "model_dir": "",
            "timeout_sec": 300,
            "jpg_quality": 95,
            "png_compression": 3,
            "max_workers": 1,
            "gpu_id": "",
            "tile_size": 0,
            "tta_mode": False,
            "auto_setup": True,
            "verify_checksum": True,
            "setup_dir": "",
            "setup_force_download": False,
            "setup_download_timeout_sec": 1200,
        }
    upscale_image_file = None

# Import persistent cache
try:
    from cache_manager import get_cache
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False
    def get_cache():
        return None

# Async runtime is opt-in for stability.
ENABLE_ASYNC_RUNTIME = os.getenv("AUTO_WHISK_ENABLE_ASYNC", "0") == "1"
try:
    import asyncio
    import aiohttp
    ASYNC_AVAILABLE = ENABLE_ASYNC_RUNTIME
except ImportError:
    ASYNC_AVAILABLE = False

# --- CONFIGURATION ---
# With curl_cffi, we don't need to manually set a fake User-Agent string 
# if we use impersonate="chrome...", but keeping a variable is fine for logging.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
OUTPUT_DIR = "output_images"

# --- RATE LIMITING SETTINGS ---
# Default strategy is now "fast-first, adaptive on 429" for large batches.
INITIAL_DELAY = float(os.getenv("AUTO_WHISK_BASE_DELAY", "0.05"))
MAX_DELAY = float(os.getenv("AUTO_WHISK_MAX_DELAY", "10.0"))
RATE_LIMIT_PAUSE = float(os.getenv("AUTO_WHISK_RATE_LIMIT_PAUSE", "8.0"))
MAX_RETRIES = 3
REFERENCE_UPLOAD_MAX_RETRIES = 3

# --- HARDCODED COOKIE ---
HARDCODED_COOKIE = "" # Removed hardcoded default to encourage login

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_FILE = os.path.join(ROOT_DIR, "configs", "config.json")

# Markers for UI progress callbacks
SKIPPED_MARKER = "__SKIPPED__"
UNSAFE_MARKER = "__UNSAFE__"
UNSAFE_RESULT = "__UNSAFE_RESULT__"
SKIPPED_RESULT = "__SKIPPED_RESULT__"

# --- ASPECT RATIO MAPPING ---
ASPECT_RATIO_MAP = {
    "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "16:9 (Landscape)": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "9:16 (Portrait)": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
    "1:1 (Square)": "IMAGE_ASPECT_RATIO_SQUARE"
}

REFERENCE_UPLOAD_URL = "https://labs.google/fx/api/trpc/backbone.uploadImage"
REFERENCE_RECIPE_URLS = [
    "https://aisandbox-pa.googleapis.com/v1/whisk:runImageRecipe",
    "https://aisandbox-pa.googleapis.com/v1:runImageRecipe",
]
METADATA_FILENAME = "generation_metadata.jsonl"
REFERENCE_SAFE_MIME_TYPES = {"image/jpeg", "image/png"}
MAX_REFERENCE_PER_ALIAS = 3
MAX_DYNAMIC_ANCHORS_PER_ALIAS = 2
OUTPUT_IMAGE_PREFIX = "image_"
GENERATION_MODE_WHISK = "whisk"
GENERATION_MODE_FLOW = "google_flow"
UPSCALE_DEFAULTS = get_default_upscale_options()
try:
    _UPSCALE_MAX_WORKERS = max(1, int(UPSCALE_DEFAULTS.get("max_workers", 1)))
except Exception:
    _UPSCALE_MAX_WORKERS = 1
UPSCALE_SEMAPHORE = th.Semaphore(_UPSCALE_MAX_WORKERS)


def normalize_generation_mode(mode):
    value = str(mode or "").strip().lower()
    if value in {"flow", "google_flow", "google-flow", "google flow"}:
        return GENERATION_MODE_FLOW
    return GENERATION_MODE_WHISK


def _mode_profile(mode):
    selected = normalize_generation_mode(mode)
    if selected == GENERATION_MODE_FLOW:
        return {
            "mode": GENERATION_MODE_FLOW,
            "label": "Google Flow",
            "session_referer": "https://labs.google/fx/tools/flow",
            "project_referer": "https://labs.google/fx/tools/flow/project",
            "project_url_prefix": "https://labs.google/fx/vi/tools/flow/project",
            "workflow_experiment_ids": ["flow", "imagefx"],
            "generate_urls": [
                "https://aisandbox-pa.googleapis.com/v1/flow:generateImage",
                "https://aisandbox-pa.googleapis.com/v1alpha/flow:generateImage",
                "https://aisandbox-pa.googleapis.com/v1/whisk:generateImage",
            ],
            "recipe_urls": [
                "https://aisandbox-pa.googleapis.com/v1/flow:runImageRecipe",
                "https://aisandbox-pa.googleapis.com/v1alpha/flow:runImageRecipe",
                "https://aisandbox-pa.googleapis.com/v1/whisk:runImageRecipe",
                "https://aisandbox-pa.googleapis.com/v1:runImageRecipe",
            ],
        }
    return {
        "mode": GENERATION_MODE_WHISK,
        "label": "Whisk",
        "session_referer": "https://labs.google/fx/tools/whisk",
        "project_referer": "https://labs.google/fx/tools/whisk/project",
        "project_url_prefix": "https://labs.google/fx/tools/whisk/project",
        "workflow_experiment_ids": ["imagefx"],
        "generate_urls": [
            "https://aisandbox-pa.googleapis.com/v1/whisk:generateImage",
        ],
        "recipe_urls": list(REFERENCE_RECIPE_URLS),
    }

# --- AUTO SUGGEST THREADS ---
def suggest_threads(num_prompts):
    """
    Auto-suggest optimal thread count based on number of prompts.
    Returns (suggested_threads, estimated_time_minutes)
    """
    if num_prompts <= 10:
        threads = 3
    elif num_prompts <= 30:
        threads = 4
    elif num_prompts <= 50:
        threads = 6
    elif num_prompts <= 100:
        threads = 8
    elif num_prompts <= 300:
        threads = 10
    elif num_prompts <= 600:
        threads = 12
    elif num_prompts <= 1000:
        threads = 14
    elif num_prompts <= 2000:
        threads = 16
    else:
        threads = 18  # keep headroom under hard UI max (20)
    
    # Estimate time is adaptive and depends on rate limit pressure.
    avg_time_per_image = 6
    estimated_seconds = (num_prompts / threads) * avg_time_per_image
    estimated_minutes = estimated_seconds / 60
    
    return threads, round(estimated_minutes, 1)


def _sleep_with_stop(seconds, stop_event=None):
    total = float(seconds or 0.0)
    if total <= 0:
        return True
    if not stop_event:
        time.sleep(total)
        return True
    end_time = time.time() + total
    while True:
        if stop_event.is_set():
            return False
        remaining = end_time - time.time()
        if remaining <= 0:
            return True
        time.sleep(min(0.25, remaining))


def build_output_filename(output_folder, index, extension=".jpg"):
    """Build deterministic output filename: image_001.jpg, image_002.jpg, ..."""
    idx = int(index)
    ext = str(extension or ".jpg").strip().lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    return os.path.join(output_folder, f"{OUTPUT_IMAGE_PREFIX}{idx:03d}{ext}")


def build_runtime_session(cookie, generation_mode=GENERATION_MODE_WHISK):
    profile = _mode_profile(generation_mode)
    session = requests.Session(impersonate="chrome131")
    base_headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
        'origin': 'https://labs.google',
        'priority': 'u=1, i',
        'referer': profile["project_referer"],
        'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
    }
    # In Flow mode the "cookie" field may actually hold a bearer token pasted by the user.
    # We must NOT put a bearer token in the Cookie header — it belongs in Authorization only.
    # For Whisk mode (or when the value is a real cookie string) we keep the original behavior.
    cookie_val = str(cookie or "").strip()
    is_flow_bearer = (
        generation_mode == GENERATION_MODE_FLOW
        and cookie_val
        and cookie_val.startswith("ya29.")
    )
    if not is_flow_bearer:
        base_headers['cookie'] = cookie_val
    session.headers.update(base_headers)
    return session


def _friendly_failure_reason(error_code):
    code = str(error_code or "").strip()
    if not code:
        return "No image received from service."

    upper = code.upper()
    lower = code.lower()
    if upper in {"UNSAFE_PROMPT", UNSAFE_MARKER}:
        return "Prompt was blocked by the safety filter."
    if upper in {"RATE_LIMIT", "HTTP_429"}:
        return "Service is busy. Please try again in a few minutes."
    if upper in {"NO_IMAGE", "WORKFLOW_EMPTY"}:
        return "Service did not return an image."
    if upper in {"NO_REFERENCE_MEDIA", "MISSING_REFERENCE_ALIAS"}:
        return "Missing reference image for one or more characters."
    if upper in {"HTTP_401", "HTTP_403"}:
        return "Access key is no longer valid."
    if upper in {"HTTP_400"}:
        return "Invalid request input."
    if "TIMEOUT" in upper:
        return "Connection timed out."
    if "CONNECTION" in upper or "CONNECT" in upper:
        return "Network connection error."
    if "SESSION FETCH" in upper:
        return "Unable to validate session."
    if "SSLEOF" in upper or "SSL" in upper:
        return "SSL connection error."
    if "ERROR" in lower or "exception" in lower:
        return "System error during generation."
    return "Image generation failed due to a system error."


def _emit_progress(progress_data, index, payload):
    if not progress_data or not progress_data.get("callback"):
        return
    with progress_data["lock"]:
        reported = progress_data.setdefault("reported", set())
        if index in reported:
            return
        reported.add(index)
        progress_data["completed"] += 1
        completed = progress_data["completed"]
        total = progress_data["total"]
    progress_data["callback"](completed, total, payload, index)


def _emit_phase_progress(progress_data, index, phase, message=""):
    if not progress_data or not progress_data.get("callback"):
        return
    with progress_data["lock"]:
        completed = progress_data.get("completed", 0)
        total = progress_data.get("total", 0)
    progress_data["callback"](
        completed,
        total,
        {
            "status": "phase",
            "phase": str(phase or "").strip().lower(),
            "message": str(message or "").strip(),
        },
        index,
    )

# --- RATE LIMIT CONTROLLER ---
class RateLimitController:
    """Thread-safe rate limit controller with adaptive delays"""
    
    def __init__(self, log_callback):
        self.lock = th.Lock()
        self.current_delay = INITIAL_DELAY
        self.consecutive_429 = 0
        self.total_429 = 0
        self.is_paused = False
        self.pause_until = 0
        self.log = log_callback
    
    def on_success(self):
        """Called when a request succeeds"""
        with self.lock:
            self.consecutive_429 = 0
            # Gradually reduce delay on success
            if self.current_delay > INITIAL_DELAY:
                self.current_delay = max(INITIAL_DELAY, self.current_delay * 0.75)
    
    def on_rate_limit(self):
        """Called when 429 is received"""
        with self.lock:
            self.consecutive_429 += 1
            self.total_429 += 1
            
            # Increase delay exponentially
            base = max(self.current_delay, 0.2)
            self.current_delay = min(MAX_DELAY, base * 1.7)
            
            # If multiple 429s, pause all requests
            if self.consecutive_429 >= 3:
                self.is_paused = True
                pause_seconds = min(MAX_DELAY * 2.0, RATE_LIMIT_PAUSE + (self.consecutive_429 - 2) * 2.0)
                self.pause_until = time.time() + pause_seconds
                self.log(f"⏸️ Rate limit hit! Pausing for {round(pause_seconds, 1)}s...")
    
    def wait_if_needed(self, stop_event=None):
        """Wait if rate limited, then return current delay"""
        pause_wait = 0.0
        with self.lock:
            if self.is_paused:
                pause_wait = max(0.0, self.pause_until - time.time())
            delay = self.current_delay + random.uniform(0.0, 0.2)

        if pause_wait > 0:
            if not _sleep_with_stop(pause_wait, stop_event):
                return 0.0
            with self.lock:
                self.is_paused = False
                self.consecutive_429 = 0
            self.log("▶️ Resuming after pause...")
        
        return delay
    
    def get_status(self):
        """Get current rate limit status"""
        with self.lock:
            return {
                'delay': round(self.current_delay, 2),
                'consecutive_429': self.consecutive_429,
                'total_429': self.total_429,
                'is_paused': self.is_paused
            }

try:
    # import whisk_core
    # RUST_AVAILABLE = True
    RUST_AVAILABLE = False # Force Python implementation for better stealth (curl_cffi)
except ImportError:
    RUST_AVAILABLE = False
    
# --- AUTO REFRESH: Get Bearer Token from Session ---
def _looks_like_bearer_token(value):
    """Return True if value is already a pre-issued bearer/access token (not a session cookie).

    Session cookies always contain semicolons separating key=value pairs (e.g.
    'SID=Abc; HSID=Xyz; ...'). Bearer tokens (OAuth2, JWT) never contain semicolons.

    NOTE: reCAPTCHA tokens (0cAFcWe...) are also long strings without semicolons,
    so we must NOT use the generic length check alone — only accept known OAuth2/JWT prefixes.
    """
    v = str(value or "").strip()
    if not v:
        return False
    # Cookie strings are always name=value pairs. Reject them early so
    # single-cookie inputs like "__Secure-next-auth.session-token=..."
    # are not mistaken for JWT/bearer tokens in Flow mode.
    if "=" in v:
        return False
    # Google OAuth2 access tokens always start with "ya29."
    if v.startswith("ya29."):
        return True
    # JWT tokens: base64url-encoded header starts with "eyJ"
    if v.startswith("eyJ"):
        return True
    # reCAPTCHA tokens start with "0cAF" — explicitly reject them
    if v.startswith("0cAF") or v.startswith("0cAf"):
        return False
    # Generic: bearer tokens are long, have no ';', no '=' (session cookies always have key=value),
    # and contain '.' (URL-safe base64 segments).
    if len(v) > 50 and ";" not in v and "=" not in v and "." in v:
        return True
    return False


def get_bearer_token_from_session(
    cookie,
    log_callback,
    on_cookie_refresh=None,
    generation_mode=GENERATION_MODE_WHISK,
):
    """Get fresh Bearer Token from Cookie via session endpoint.
    Also captures Set-Cookie headers to auto-refresh the stored cookie.
    """
    # For Flow mode: if the caller already pasted a bearer token directly,
    # skip the session exchange and use it as-is.
    if generation_mode == GENERATION_MODE_FLOW and _looks_like_bearer_token(cookie):
        log_callback("✅ Bearer Token accepted directly (Google Flow mode)")
        return cookie

    if RUST_AVAILABLE:
        try:
            client = whisk_core.WhiskClient(cookie)
            token = client.get_bearer_token()
            log_callback(f"✅ [Rust] Bearer Token fetched")
            return token
        except Exception as e:
            log_callback(f"⚠️ [Rust] Token fetch error: {e}")
            # Fallback to python? 
            pass

    # Python Fallback
    SESSION_URL = "https://labs.google/fx/api/auth/session"
    
    profile = _mode_profile(generation_mode)
    headers = {
        'accept': '*/*',
        'content-type': 'application/json',
        'cookie': cookie,
        'referer': profile["session_referer"],
    }
    
    try:
        response = requests.get(SESSION_URL, headers=headers, timeout=30, impersonate="chrome120")
        
        if response.status_code == 200:
            data = response.json()
            access_token = data.get('access_token')
            
            if access_token:
                log_callback(f"✅ Bearer Token fetched (expires: {data.get('expires', 'unknown')})")
                
                # --- AUTO REFRESH COOKIE ---
                # Check if Google returned new cookies in response headers
                new_cookies = response.headers.get('Set-Cookie') or response.headers.get('set-cookie')
                if new_cookies:
                    try:
                        refreshed_cookie = _auto_refresh_cookie(cookie, new_cookies, log_callback)
                        if refreshed_cookie and on_cookie_refresh:
                            on_cookie_refresh(refreshed_cookie)
                    except Exception as e:
                        log_callback(f"⚠️ Access key refresh failed: {e}")
                
                return access_token
            else:
                log_callback("⚠️ No access_token in session - Access key may be expired (or needs browser refresh)")
                return None
        else:
            log_callback(f"⚠️ Session fetch failed: {response.status_code}")
            return None
            
    except Exception as e:
        log_callback(f"❌ Session fetch error: {e}")
        return None


def _auto_refresh_cookie(old_cookie, set_cookie_header, log_callback):
    """Parse Set-Cookie header and update config.json if new session token found."""
    import json
    import os
    
    # Parse the new cookies from Set-Cookie header
    # Format can be: "name=value; Path=/; Secure; ..." or multiple cookies
    new_cookies_dict = {}
    
    # Handle both single string and list of cookies
    if isinstance(set_cookie_header, str):
        cookie_items = [set_cookie_header]
    else:
        cookie_items = set_cookie_header
    
    for item in cookie_items:
        # Extract name=value (first part before semicolon)
        if '=' in item:
            parts = item.split(';')[0].strip()
            if '=' in parts:
                name, value = parts.split('=', 1)
                new_cookies_dict[name] = value
    
    # Parse old cookie into dict
    old_cookies_dict = {}
    for part in old_cookie.split(';'):
        if '=' in part:
            name, value = part.strip().split('=', 1)
            old_cookies_dict[name] = value

    # Check if we got a fresh session token.
    session_token_name = "__Secure-next-auth.session-token"
    new_session_token = new_cookies_dict.get(session_token_name)
    if not new_session_token:
        return None
    old_session_token = old_cookies_dict.get(session_token_name)
    if old_session_token == new_session_token:
        return None

    log_callback("🔄 New session key detected, auto-refreshing...")
    
    # Merge: new cookies override old ones
    old_cookies_dict.update(new_cookies_dict)
    
    # Rebuild cookie string
    new_cookie_string = "; ".join([f"{k}={v}" for k, v in old_cookies_dict.items()])
    
    # Save to config.json
    config_path = CONFIG_FILE
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except:
            pass
    
    config['cookie'] = new_cookie_string
    
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    
    log_callback("✅ Access key auto-refreshed and saved!")
    return new_cookie_string

def _normalize_alias(alias):
    if alias is None:
        return ""
    return str(alias).strip()


def _alias_key(alias):
    return _normalize_alias(alias).lower()


def _sha1_file(path):
    hasher = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _guess_mime_type(path):
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        return "image/jpeg"
    if not mime_type.startswith("image/"):
        return "image/jpeg"
    return mime_type


def _encode_image_data_uri(path):
    mime_type = _guess_mime_type(path)
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}", mime_type


def _encode_image_as_jpeg_data_uri(path, max_side=2048, quality=90):
    """Fallback encoder to improve compatibility for upload endpoint."""
    try:
        import cv2
        import numpy as np

        raw = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if image is None:
            return None, None

        height, width = image.shape[:2]
        longest = max(width, height)
        if longest > max_side:
            scale = max_side / float(longest)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)

        ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            return None, None

        encoded_b64 = base64.b64encode(encoded.tobytes()).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded_b64}", "image/jpeg"
    except Exception:
        return None, None


def _short_response_body(response, limit=320):
    try:
        text = response.text or ""
    except Exception:
        return ""
    text = text.replace("\n", " ").replace("\r", " ").strip()
    return text[:limit]


def _short_response_body_json(data, limit=400):
    """Compact JSON preview of a dict/list for debug logging."""
    try:
        text = json.dumps(data, separators=(",", ":"))
        return text[:limit]
    except Exception:
        return str(data)[:limit]


def _extract_prompt_aliases(prompt, aliases):
    if not prompt or not aliases:
        return []

    alias_map = {_alias_key(name): _normalize_alias(name) for name in aliases if _normalize_alias(name)}
    if not alias_map:
        return []

    matches = []
    seen = set()

    explicit_pattern = re.compile(r"@([A-Za-z0-9_-]+)")
    for item in explicit_pattern.finditer(prompt):
        token = _alias_key(item.group(1))
        if token in alias_map and token not in seen:
            seen.add(token)
            matches.append((item.start(), alias_map[token]))

    for key, alias in alias_map.items():
        if key in seen:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE)
        hit = pattern.search(prompt)
        if hit:
            seen.add(key)
            matches.append((hit.start(), alias))

    matches.sort(key=lambda x: x[0])
    return [alias for _, alias in matches]


def _normalize_consistency_level(level):
    value = str(level or "standard").strip().lower()
    if value in {"high", "very_high"}:
        return value
    return "standard"


def _seed_from_aliases(aliases):
    normalized = sorted({_alias_key(a) for a in (aliases or []) if _alias_key(a)})
    if not normalized:
        return random.randint(100000, 999999)
    raw = "|".join(normalized).encode("utf-8")
    digest = hashlib.sha1(raw).hexdigest()
    return 100000 + (int(digest[:8], 16) % 900000)


def _build_identity_lock_prompt(prompt, aliases, consistency_level="standard"):
    base = str(prompt or "").strip()
    clean_aliases = [str(a).strip() for a in (aliases or []) if str(a).strip()]
    if not clean_aliases:
        return base
    alias_text = ", ".join(clean_aliases)
    level = _normalize_consistency_level(consistency_level)
    if level == "very_high":
        suffix = (
            f" Keep the same character identity for {alias_text} exactly as the reference image. "
            "Identity lock is strict: keep same face shape, eye shape, nose, mouth, hairline, hairstyle, "
            "skin tone, glasses/accessories, and body proportions. "
            "Do not change age, gender, ethnicity, or facial structure. Do not swap to another person."
        )
    elif level == "high":
        suffix = (
            f" Keep the same character identity for {alias_text} as the reference image: "
            "same face, hair, eyes, glasses/accessories, and body proportions. "
            "Do not change age, gender, or facial structure."
        )
    else:
        suffix = (
            f" Keep character identity for {alias_text} consistent with the reference image."
        )
    return f"{base}{suffix}"


def _resolve_seed(seed=None):
    try:
        if seed is not None:
            seed_value = int(seed)
            if seed_value >= 0:
                return seed_value
    except Exception:
        pass
    return random.randint(100000, 999999)


def _first_generated_image(data):
    if not isinstance(data, dict):
        return {}
    image_panels = data.get("imagePanels", [])
    if not image_panels or not isinstance(image_panels[0], dict):
        return {}
    generated_images = image_panels[0].get("generatedImages", [])
    if not generated_images or not isinstance(generated_images[0], dict):
        return {}
    return generated_images[0]


def _build_runtime_options(
    seed_mode="random",
    fixed_seed=None,
    consistency_level="standard",
    upscale_options=None,
    generation_mode=GENERATION_MODE_WHISK,
    flow_project_id="",
    flow_session_cookie="",
    flow_model_name="BANANA_PRO_2",
):
    mode = str(seed_mode or "random").strip().lower()
    if mode not in {"random", "fixed"}:
        mode = "random"

    parsed_seed = None
    if fixed_seed is not None and str(fixed_seed).strip() != "":
        try:
            value = int(str(fixed_seed).strip())
            if value >= 0:
                parsed_seed = value
        except Exception:
            parsed_seed = None

    if mode == "fixed" and parsed_seed is None:
        mode = "random"

    defaults = dict(UPSCALE_DEFAULTS or {})
    provided = dict(upscale_options or {}) if isinstance(upscale_options, dict) else {}

    def _pick(key, fallback=None):
        if key in provided and provided.get(key) is not None:
            return provided.get(key)
        if key in defaults:
            return defaults.get(key)
        return fallback

    upscale_engine = str(_pick("engine", "auto") or "auto").strip().lower()
    if upscale_engine not in {"auto", "realesrgan", "opencv"}:
        upscale_engine = "auto"

    upscale_mode = str(_pick("mode", "replace") or "replace").strip().lower()
    if upscale_mode not in {"replace", "keep_both"}:
        upscale_mode = "replace"

    try:
        upscale_scale = int(_pick("scale", 2))
    except Exception:
        upscale_scale = 2
    upscale_scale = max(1, min(upscale_scale, 8))

    def _as_int(value, default):
        try:
            return int(value)
        except Exception:
            return int(default)

    def _as_bool(value, default=False):
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return bool(value)

    def _normalize_upscale_target(value):
        raw = str(value or "").strip().lower()
        if raw in {"1080", "1080p", "fhd", "full hd", "fullhd"}:
            return "1080p"
        if raw in {"2k", "1440p", "qhd"}:
            return "2K"
        if raw in {"4k", "2160p", "uhd"}:
            return "4K"
        return ""

    return {
        "seed_mode": mode,
        "fixed_seed": parsed_seed,
        "consistency_level": _normalize_consistency_level(consistency_level),
        "generation_mode": normalize_generation_mode(generation_mode),
        "flow_project_id": str(flow_project_id or "").strip(),
        "flow_session_cookie": str(flow_session_cookie or "").strip(),
        "flow_model_name": str(flow_model_name or "").strip() or "BANANA_PRO_2",
        "upscale_enabled": _as_bool(_pick("enabled", False), False),
        "upscale_scale": upscale_scale,
        "upscale_target_preset": _normalize_upscale_target(_pick("target_preset", "")),
        "upscale_target_width": max(0, _as_int(_pick("target_width", 0), 0)),
        "upscale_target_height": max(0, _as_int(_pick("target_height", 0), 0)),
        "upscale_engine": upscale_engine,
        "upscale_mode": upscale_mode,
        "upscale_keep_raw": _as_bool(_pick("keep_raw", False), False),
        "upscale_bin_path": str(_pick("bin_path", "") or "").strip(),
        "upscale_model_name": str(_pick("model_name", "") or "").strip(),
        "upscale_model_dir": str(_pick("model_dir", "") or "").strip(),
        "upscale_timeout_sec": max(10, _as_int(_pick("timeout_sec", 300), 300)),
        "upscale_jpg_quality": max(60, min(100, _as_int(_pick("jpg_quality", 95), 95))),
        "upscale_png_compression": max(0, min(9, _as_int(_pick("png_compression", 3), 3))),
        "upscale_gpu_id": str(_pick("gpu_id", "") or "").strip(),
        "upscale_tile_size": max(0, _as_int(_pick("tile_size", 0), 0)),
        "upscale_tta_mode": _as_bool(_pick("tta_mode", False), False),
        "upscale_auto_setup": _as_bool(_pick("auto_setup", True), True),
        "upscale_verify_checksum": _as_bool(_pick("verify_checksum", True), True),
        "upscale_setup_dir": str(_pick("setup_dir", "") or "").strip(),
        "upscale_setup_force_download": _as_bool(_pick("setup_force_download", False), False),
        "upscale_setup_download_timeout_sec": max(30, _as_int(_pick("setup_download_timeout_sec", 1200), 1200)),
    }


def _append_metadata_line(runtime_options, metadata, log_callback):
    if not runtime_options or not metadata:
        return

    meta_path = runtime_options.get("metadata_path")
    meta_lock = runtime_options.get("metadata_lock")
    if not meta_path or not meta_lock:
        return

    try:
        with meta_lock:
            with open(meta_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(metadata, ensure_ascii=False) + "\n")
    except Exception as exc:
        if log_callback:
            log_callback(f"⚠️ Failed to write metadata: {exc}")


def _parse_uploaded_media_id(data):
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            media_id = (
                item.get("result", {})
                .get("data", {})
                .get("json", {})
                .get("mediaGenerationId")
            )
            if media_id:
                return media_id
            return (
                item.get("result", {})
                .get("data", {})
                .get("json", {})
                .get("result", {})
                .get("uploadMediaGenerationId")
            )
    if isinstance(data, dict):
        media_id = (
            data.get("result", {})
            .get("data", {})
            .get("json", {})
            .get("mediaGenerationId")
        )
        if media_id:
            return media_id
        return (
            data.get("result", {})
            .get("data", {})
            .get("json", {})
            .get("result", {})
            .get("uploadMediaGenerationId")
        )
    return None


def upload_reference_image(session, image_path, log_callback, category="MEDIA_CATEGORY_SUBJECT"):
    if not image_path or not os.path.isfile(image_path):
        return None, "FILE_NOT_FOUND"
    
    # Check persistent cache first
    if CACHE_AVAILABLE:
        try:
            image_hash = _sha1_file(image_path)
            cache = get_cache()
            if cache:
                cached_media_id = cache.get(image_hash)
                if cached_media_id:
                    log_callback(f"📦 Using cached reference (hash: {image_hash[:8]}...)")
                    return cached_media_id, None
        except Exception:
            pass

    original_data_uri, original_mime = _encode_image_data_uri(image_path)
    fallback_data_uri = None
    fallback_mime = None

    mode = "original"
    for attempt in range(1, REFERENCE_UPLOAD_MAX_RETRIES + 1):
        use_data_uri = original_data_uri
        use_mime = original_mime

        if mode == "jpeg-fallback":
            if not fallback_data_uri:
                fallback_data_uri, fallback_mime = _encode_image_as_jpeg_data_uri(image_path)
            if fallback_data_uri and fallback_mime:
                use_data_uri = fallback_data_uri
                use_mime = fallback_mime
            else:
                mode = "original"

        payload = {
            "json": {
                "clientContext": {
                    "workflowId": str(uuid.uuid4()),
                    "sessionId": str(int(time.time() * 1000)),
                },
                "uploadMediaInput": {
                    "mediaCategory": category,
                    "rawBytes": use_data_uri,
                    "caption": "",
                },
            }
        }

        try:
            response = session.post(REFERENCE_UPLOAD_URL, json=payload, timeout=90)
        except Exception as exc:
            if attempt < REFERENCE_UPLOAD_MAX_RETRIES:
                log_callback(
                    f"Warning: upload attempt {attempt}/{REFERENCE_UPLOAD_MAX_RETRIES} failed ({mode}): {exc}"
                )
                time.sleep(min(3.0, 0.6 * attempt))
                continue
            return None, str(exc)

        if response.status_code == 200:
            try:
                data = response.json()
            except Exception as exc:
                if attempt < REFERENCE_UPLOAD_MAX_RETRIES:
                    log_callback(
                        f"Warning: upload attempt {attempt}/{REFERENCE_UPLOAD_MAX_RETRIES} invalid JSON ({mode}): {exc}"
                    )
                    time.sleep(min(3.0, 0.6 * attempt))
                    continue
                return None, f"INVALID_JSON:{exc}"

            media_id = _parse_uploaded_media_id(data)
            if not media_id:
                log_callback(f"Warning: upload response has no mediaGenerationId: {str(data)[:300]}")
                if attempt < REFERENCE_UPLOAD_MAX_RETRIES:
                    time.sleep(min(3.0, 0.6 * attempt))
                    continue
                return None, "NO_MEDIA_ID"
            
            # Save to persistent cache
            if CACHE_AVAILABLE:
                try:
                    cache = get_cache()
                    if cache:
                        image_hash = _sha1_file(image_path)
                        cache.set(image_hash, media_id, image_path)
                        log_callback(f"💾 Cached reference (hash: {image_hash[:8]}...)")
                except Exception:
                    pass
            
            return media_id, None

        detail = _short_response_body(response)
        detail_msg = f" | detail: {detail}" if detail else ""
        log_callback(
            f"Warning: upload attempt {attempt}/{REFERENCE_UPLOAD_MAX_RETRIES} got HTTP_{response.status_code} ({mode}){detail_msg}"
        )

        should_switch_to_fallback = (
            mode == "original"
            and (response.status_code >= 500 or response.status_code in {400, 413, 415, 422})
        )
        if should_switch_to_fallback:
            if not fallback_data_uri:
                fallback_data_uri, fallback_mime = _encode_image_as_jpeg_data_uri(image_path)
            if fallback_data_uri and fallback_mime:
                mode = "jpeg-fallback"
                log_callback("Info: switching reference upload to JPEG fallback.")

        if attempt < REFERENCE_UPLOAD_MAX_RETRIES:
            time.sleep(min(3.0, 0.6 * attempt))
            continue
        return None, f"HTTP_{response.status_code}"

    return None, "UPLOAD_RETRY_EXHAUSTED"


def prepare_character_registry(session, character_refs, log_callback):
    registry = {
        "aliases": {},
        "hash_cache": {},
        "lock": th.Lock(),
    }
    if not character_refs:
        return registry

    for item in character_refs:
        alias = _normalize_alias(item.get("alias"))
        path = _normalize_alias(item.get("path"))
        if not alias:
            continue
        alias_key = _alias_key(alias)
        if alias_key not in registry["aliases"]:
            registry["aliases"][alias_key] = {
                "alias": alias,
                "paths": [],
                "media_ids": [],
                "dynamic_media_ids": [],
            }
        entry = registry["aliases"][alias_key]
        if path:
            entry["paths"].append(path)

        if not path:
            log_callback(f"Warning: alias '{alias}' has no image path.")
            continue
        if not os.path.isfile(path):
            log_callback(f"Warning: alias '{alias}' image not found: {path}")
            continue

        try:
            image_hash = _sha1_file(path)
        except Exception as exc:
            log_callback(f"Error: cannot read image for alias '{alias}': {exc}")
            continue

        cached_media = registry["hash_cache"].get(image_hash)
        if cached_media:
            if cached_media not in entry["media_ids"]:
                entry["media_ids"].append(cached_media)
            continue

        log_callback(f"Uploading reference for alias '{alias}'...")
        media_id, error = upload_reference_image(session, path, log_callback)
        if error:
            log_callback(f"Error: upload failed for alias '{alias}': {error}")
            continue

        registry["hash_cache"][image_hash] = media_id
        if media_id not in entry["media_ids"]:
            entry["media_ids"].append(media_id)
        log_callback(f"Alias '{alias}' is ready.")

    return registry


# --- STEP 1: Create Workflow ---
def create_workflow(
    session,
    prompt,
    log_callback,
    subject_media_ids=None,
    user_instruction=None,
    generation_mode=GENERATION_MODE_WHISK,
):
    """Create workflow via tRPC API"""
    # If session is a Rust Client
    if hasattr(session, 'create_workflow'):
        try:
            return session.create_workflow(prompt), None # Rust returns string or raises exception
        except Exception as e:
            return None, str(e)

    API_URL = "https://labs.google/fx/api/trpc/media.createOrUpdateWorkflow"
    profile = _mode_profile(generation_mode)

    last_error = "WORKFLOW_CREATE_FAILED"
    for exp_id in profile["workflow_experiment_ids"]:
        payload = {
            "json": {
                "prompt": prompt,
                "experimentId": exp_id,
            }
        }
        if user_instruction:
            payload["json"]["userInstruction"] = user_instruction
        if subject_media_ids:
            payload["json"]["sources"] = [
                {
                    "category": "MEDIA_CATEGORY_SUBJECT",
                    "mediaGenerationId": media_id,
                }
                for media_id in subject_media_ids
                if media_id
            ]

        try:
            response = session.post(API_URL, json=payload, timeout=60)
        except Exception as e:
            last_error = str(e)
            continue

        if response.status_code == 200:
            data = response.json()
            workflow_id = data.get("result", {}).get("data", {}).get("json", {}).get("result", {}).get("workflowId")
            return workflow_id, None
        if response.status_code == 429:
            return None, "RATE_LIMIT"
        last_error = f"HTTP_{response.status_code}"
        if response.status_code == 400 and exp_id != profile["workflow_experiment_ids"][-1]:
            continue
        if response.status_code in {401, 403}:
            break

    return None, last_error

# --- ERROR EXTRACTION ---
def _extract_error_reason(response):
    try:
        data = response.json()
    except Exception:
        return None
    details = data.get("error", {}).get("details", [])
    for item in details:
        if item.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo":
            reason = item.get("reason")
            if reason:
                return reason
    return None


def _collect_media_name_candidates(payload):
    keys = {
        "name",
        "mediaGenerationId",
        "media_generation_id",
        "mediaName",
        "outputMediaGenerationId",
    }
    found = []
    seen = set()

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key or "") in keys and isinstance(value, str):
                    text = value.strip()
                    if text and text not in seen:
                        seen.add(text)
                        found.append(text)
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return found


def _download_flow_media_via_redirect(
    media_name,
    log_callback,
    session=None,
    generation_mode=GENERATION_MODE_FLOW,
    flow_project_id="",
):
    if not media_name:
        return None, "FLOW_MEDIA_NAME_EMPTY"

    profile = _mode_profile(generation_mode)
    project_id = str(flow_project_id or "").strip()
    if project_id:
        referer = f"{profile['project_url_prefix']}/{project_id}"
    else:
        referer = profile["project_referer"]

    url = "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={name}".format(
        name=quote(str(media_name), safe="")
    )
    headers = {
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
        if session:
            response = session.get(url, headers=headers, timeout=60)
        else:
            response = requests.get(url, headers=headers, timeout=60, impersonate="chrome131")
    except Exception as exc:
        return None, f"FLOW_MEDIA_REDIRECT_ERROR: {exc}"

    content_type = str(response.headers.get("content-type", "")).lower()
    if response.status_code == 200 and content_type.startswith("image/") and response.content:
        return bytes(response.content), None

    if response.status_code in {301, 302, 303, 307, 308}:
        location = str(response.headers.get("location", "")).strip()
        if location:
            try:
                if session:
                    redirected = session.get(location, headers={"accept": headers["accept"]}, timeout=60)
                else:
                    redirected = requests.get(
                        location,
                        headers={"accept": headers["accept"]},
                        timeout=60,
                        impersonate="chrome131",
                    )
            except Exception as exc:
                return None, f"FLOW_MEDIA_REDIRECT_FOLLOW_ERROR: {exc}"
            redirected_type = str(redirected.headers.get("content-type", "")).lower()
            if redirected.status_code == 200 and redirected_type.startswith("image/") and redirected.content:
                return bytes(redirected.content), None
            return None, f"FLOW_MEDIA_REDIRECT_HTTP_{redirected.status_code}"

    if log_callback:
        body = _short_response_body(response)
        if body:
            log_callback(f"Flow media redirect detail: {body}")
    return None, f"FLOW_MEDIA_REDIRECT_HTTP_{response.status_code}"


def generate_image_with_flow_batch(
    prompt,
    ratio,
    bearer_token,
    log_callback,
    session=None,
    seed=None,
    runtime_options=None,
    reference_inputs=None,
):
    options = runtime_options or {}
    project_id = str(options.get("flow_project_id") or "").strip()
    if not project_id:
        return None, "FLOW_PROJECT_ID_MISSING", None
    aspect_ratio = ASPECT_RATIO_MAP.get(ratio, "IMAGE_ASPECT_RATIO_SQUARE")
    seed_value = _resolve_seed(seed)
    requested_model = str(options.get("flow_model_name") or "").strip() or "BANANA_PRO_2"
    model_candidates = []
    for candidate in (requested_model, "NARWHAL"):
        text = str(candidate or "").strip()
        if text and text not in model_candidates:
            model_candidates.append(text)

    url = f"https://aisandbox-pa.googleapis.com/v1/projects/{project_id}/flowMedia:batchGenerateImages"
    token_preview = str(bearer_token or "")[:16] + "..." if len(str(bearer_token or "")) > 16 else str(bearer_token or "")
    log_callback(f"[Flow] token={token_preview} project={project_id[:12]}...")
    headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "authorization": f"Bearer {bearer_token}",
        "content-type": "text/plain;charset=UTF-8",
        "origin": "https://labs.google",
        "referer": "https://labs.google/",
        "user-agent": USER_AGENT,
    }
    # Only forward the session cookie if it looks like a real browser cookie string
    # (contains '=', not a bare bearer token pasted by the user).
    if session and "cookie" in session.headers and "cookie" not in headers:
        raw_cookie = session.headers.get("cookie", "")
        if raw_cookie and "=" in raw_cookie and not _looks_like_bearer_token(raw_cookie):
            headers["cookie"] = raw_cookie

    session_id = f";{int(time.time() * 1000)}"
    base_context = {
        "projectId": project_id,
        "tool": "PINHOLE",
        "sessionId": session_id,
    }
    image_inputs = []
    for item in reference_inputs or []:
        if isinstance(item, dict):
            media_id = str(item.get("media_id") or "").strip()
        else:
            media_id = str(item or "").strip()
        if media_id:
            image_inputs.append({"imageInputType": "IMAGE_INPUT_TYPE_REFERENCE", "name": media_id})

    data = {}
    selected_model = model_candidates[0]
    last_error = None
    for model_name in model_candidates:
        selected_model = model_name
        payload = {
            "clientContext": dict(base_context),
            "mediaGenerationContext": {
                "batchId": str(uuid.uuid4()),
            },
            "useNewMedia": True,
            "requests": [
                {
                    "clientContext": dict(base_context),
                    "imageModelName": model_name,
                    "imageAspectRatio": aspect_ratio,
                    "structuredPrompt": {
                        "parts": [{"text": str(prompt or "")}],
                    },
                    "seed": seed_value,
                    "imageInputs": image_inputs,
                }
            ],
        }

        body = json.dumps(payload, separators=(",", ":"))
        try:
            if session:
                response = session.post(url, headers=headers, data=body, timeout=180)
            else:
                response = requests.post(url, headers=headers, data=body, timeout=180, impersonate="chrome131")
        except Exception as exc:
            last_error = str(exc)
            continue

        log_callback(f"[Flow] HTTP {response.status_code} for model={model_name}")
        if response.status_code != 200:
            detail = _short_response_body(response)
            if detail:
                log_callback(f"[Flow] Response: {detail}")

        if response.status_code == 429:
            return None, "RATE_LIMIT", None

        if response.status_code == 400:
            reason = _extract_error_reason(response)
            if reason == "PUBLIC_ERROR_UNSAFE_GENERATION":
                log_callback("🚫 Prompt blocked by safety filter (PUBLIC_ERROR_UNSAFE_GENERATION).")
                return None, "UNSAFE_PROMPT", None
            detail = _short_response_body(response)
            if detail:
                log_callback(f"Flow 400 detail ({model_name}): {detail}")
            last_error = "HTTP_400"
            if model_name != model_candidates[-1]:
                log_callback(f"Flow model fallback: {model_name} -> {model_candidates[-1]}")
                continue
            return None, "HTTP_400", None

        if response.status_code in {401, 403}:
            return None, f"HTTP_{response.status_code}", None

        if response.status_code != 200:
            detail = _short_response_body(response)
            if detail:
                log_callback(f"Flow error detail ({response.status_code}, {model_name}): {detail}")
            last_error = f"HTTP_{response.status_code}"
            if model_name != model_candidates[-1]:
                continue
            return None, f"HTTP_{response.status_code}", None

        try:
            data = response.json()
        except Exception:
            data = {}
        break

    if not data:
        return None, (last_error or "FLOW_BATCH_EMPTY"), None

    generated = _first_generated_image(data)
    encoded_image = generated.get("encodedImage")
    if encoded_image:
        metadata = {
            "seed": generated.get("seed", seed_value),
            "mediaGenerationId": generated.get("mediaGenerationId"),
            "prompt": generated.get("prompt", prompt),
            "imageModel": generated.get("imageModel", selected_model),
            "workflowId": generated.get("workflowId", ""),
            "aspectRatio": generated.get("aspectRatio", aspect_ratio),
        }
        return base64.b64decode(encoded_image), None, metadata

    # 200 OK but no inline image — log top-level keys so we can diagnose response format
    top_keys = list(data.keys()) if isinstance(data, dict) else []
    log_callback(f"[Flow] 200 OK but no encodedImage. Top-level keys: {top_keys}")
    if top_keys:
        log_callback(f"[Flow] Response preview: {_short_response_body_json(data)}")

    candidates = _collect_media_name_candidates(data)
    if not candidates:
        return None, "FLOW_MEDIA_NAME_MISSING", None

    for media_name in candidates:
        image_bytes, download_error = _download_flow_media_via_redirect(
            media_name,
            log_callback,
            session=session,
            generation_mode=GENERATION_MODE_FLOW,
            flow_project_id=project_id,
        )
        if image_bytes:
            metadata = {
                "seed": seed_value,
                "mediaGenerationId": media_name,
                "prompt": prompt,
                "imageModel": selected_model,
                "workflowId": "",
                "aspectRatio": aspect_ratio,
            }
            return image_bytes, None, metadata
        if download_error and "HTTP_4" in str(download_error):
            continue

    return None, "FLOW_MEDIA_DOWNLOAD_FAILED", None

# --- STEP 2: Generate Image ---
def generate_image_from_workflow(
    workflow_id,
    prompt,
    ratio,
    bearer_token,
    log_callback,
    session=None,
    seed=None,
    generation_mode=GENERATION_MODE_WHISK,
):
    """Generate image via aisandbox API. Returns (image_bytes, error_code, metadata)"""
    
    # Rust Path
    if session and hasattr(session, 'generate_image'):
        try:
            data = session.generate_image(workflow_id, prompt, bearer_token, ratio)
            # data is now bytes (Vec<u8>)
            # Convert if needed? No, python receives bytes.
            return bytes(data), None, {
                "seed": _resolve_seed(seed),
                "workflowId": workflow_id,
                "prompt": prompt,
                "aspectRatio": ASPECT_RATIO_MAP.get(ratio, "IMAGE_ASPECT_RATIO_LANDSCAPE"),
            }
        except Exception as e:
             return None, str(e), None

    profile = _mode_profile(generation_mode)
    aspect_ratio = ASPECT_RATIO_MAP.get(ratio, "IMAGE_ASPECT_RATIO_LANDSCAPE")
    seed_value = _resolve_seed(seed)
    
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'authorization': f'Bearer {bearer_token}',
        'content-type': 'application/json',
        'origin': 'https://labs.google',
        'priority': 'u=1, i',
        'referer': profile["session_referer"],
        'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'cross-site',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        'x-browser-channel': 'stable',
        'x-browser-copyright': 'Copyright 2026 Google LLC. All Rights reserved.',
        'x-browser-year': '2026',
        'x-client-data': 'CKC1yQEIj7bJAQiltskBCKmdygEIyIfLAQiSocsBCIagzQEIlKTPAQ==',
    }
    
    payload = {
        "clientContext": {
            "workflowId": workflow_id,
            "tool": "BACKBONE",
            "sessionId": f";{int(time.time()*1000)}"
        },
        "imageModelSettings": {
            "imageModel": "IMAGEN_3_5",
            "aspectRatio": aspect_ratio
        },
        "seed": seed_value,
        "prompt": prompt,
        "mediaCategory": "MEDIA_CATEGORY_BOARD"
    }

    if session and "cookie" in session.headers and "cookie" not in headers:
        raw_cookie = session.headers.get("cookie", "")
        if raw_cookie and "=" in raw_cookie and not _looks_like_bearer_token(raw_cookie):
            headers["cookie"] = raw_cookie

    last_error = None
    for idx, api_url in enumerate(profile["generate_urls"]):
        try:
            if session:
                response = session.post(api_url, headers=headers, json=payload, timeout=120)
            else:
                response = requests.post(api_url, headers=headers, json=payload, timeout=120, impersonate="chrome131")
        except Exception as e:
            last_error = str(e)
            continue

        if response.status_code == 200:
            data = response.json()
            generated = _first_generated_image(data)
            encoded_image = generated.get("encodedImage")
            if encoded_image:
                metadata = {
                    "seed": generated.get("seed", seed_value),
                    "mediaGenerationId": generated.get("mediaGenerationId"),
                    "prompt": generated.get("prompt", prompt),
                    "imageModel": generated.get("imageModel"),
                    "workflowId": generated.get("workflowId", workflow_id),
                    "aspectRatio": generated.get("aspectRatio", aspect_ratio),
                }
                return base64.b64decode(encoded_image), None, metadata
            return None, "NO_IMAGE", None
        if response.status_code == 429:
            return None, "RATE_LIMIT", None
        if response.status_code == 400:
            reason = _extract_error_reason(response)
            if reason == "PUBLIC_ERROR_UNSAFE_GENERATION":
                log_callback("🚫 Prompt blocked by safety filter (PUBLIC_ERROR_UNSAFE_GENERATION).")
                return None, "UNSAFE_PROMPT", None
            detail = response.text[:500].strip()
            if detail:
                log_callback(f"⚠️ 400 detail: {detail}")
            if idx < len(profile["generate_urls"]) - 1:
                continue
            return None, "HTTP_400", None
        if response.status_code == 404 and idx < len(profile["generate_urls"]) - 1:
            log_callback(f"Info: endpoint not found at {api_url}, trying fallback.")
            continue
        last_error = f"HTTP_{response.status_code}"
        if response.status_code in {401, 403}:
            break
        if idx < len(profile["generate_urls"]) - 1:
            continue
        return None, last_error, None

    return None, (last_error or "GENERATE_ENDPOINT_UNAVAILABLE"), None


def generate_image_with_recipe(
    prompt,
    ratio,
    bearer_token,
    reference_inputs,
    log_callback,
    session=None,
    seed=None,
    generation_mode=GENERATION_MODE_WHISK,
):
    if not reference_inputs:
        return None, "NO_REFERENCE_MEDIA", None

    aspect_ratio = ASPECT_RATIO_MAP.get(ratio, "IMAGE_ASPECT_RATIO_SQUARE")
    seed_value = _resolve_seed(seed)
    recipe_workflow_id = str(uuid.uuid4())

    profile = _mode_profile(generation_mode)
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'authorization': f'Bearer {bearer_token}',
        'content-type': 'application/json',
        'origin': 'https://labs.google',
        'referer': profile["session_referer"],
        'user-agent': USER_AGENT,
    }

    payload = {
        "clientContext": {
            "workflowId": recipe_workflow_id,
            "tool": "BACKBONE",
            "sessionId": f"{int(time.time() * 1000)}",
        },
        "imageModelSettings": {
            "imageModel": "GEM_PIX",
            "aspectRatio": aspect_ratio,
        },
        "seed": seed_value,
        "userInstruction": prompt,
        "recipeMediaInputs": [
            {
                "caption": (item.get("caption", "") if isinstance(item, dict) else ""),
                "mediaInput": {
                    "mediaCategory": "MEDIA_CATEGORY_SUBJECT",
                    "mediaGenerationId": (
                        item.get("media_id")
                        if isinstance(item, dict)
                        else str(item)
                    ),
                },
            }
            for item in reference_inputs
            if (
                (isinstance(item, dict) and item.get("media_id"))
                or (not isinstance(item, dict) and item)
            )
        ],
    }

    if session and "cookie" in session.headers and "cookie" not in headers:
        raw_cookie = session.headers.get("cookie", "")
        if raw_cookie and "=" in raw_cookie and not _looks_like_bearer_token(raw_cookie):
            headers["cookie"] = raw_cookie

    last_error = None
    recipe_urls = profile["recipe_urls"]
    for idx, recipe_url in enumerate(recipe_urls):
        try:
            if session:
                response = session.post(recipe_url, headers=headers, json=payload, timeout=120)
            else:
                response = requests.post(
                    recipe_url,
                    headers=headers,
                    json=payload,
                    timeout=120,
                    impersonate="chrome131",
                )
        except Exception as exc:
            last_error = str(exc)
            continue

        if response.status_code == 200:
            data = response.json()
            generated = _first_generated_image(data)
            encoded_image = generated.get("encodedImage")
            if encoded_image:
                metadata = {
                    "seed": generated.get("seed", seed_value),
                    "mediaGenerationId": generated.get("mediaGenerationId"),
                    "prompt": generated.get("prompt", prompt),
                    "imageModel": generated.get("imageModel"),
                    "workflowId": generated.get("workflowId", recipe_workflow_id),
                    "aspectRatio": generated.get("aspectRatio", aspect_ratio),
                }
                return base64.b64decode(encoded_image), None, metadata
            return None, "NO_IMAGE", None
        if response.status_code == 429:
            return None, "RATE_LIMIT", None
        if response.status_code == 400:
            reason = _extract_error_reason(response)
            if reason == "PUBLIC_ERROR_UNSAFE_GENERATION":
                log_callback("Unsafe prompt detected by safety filter.")
                return None, "UNSAFE_PROMPT", None
            detail = response.text[:500].strip()
            if detail:
                log_callback(f"400 detail: {detail}")
            return None, "HTTP_400", None
        if response.status_code == 404 and idx < len(recipe_urls) - 1:
            log_callback(f"Info: recipe endpoint not found at {recipe_url}, trying fallback.")
            continue

        detail = _short_response_body(response)
        if detail:
            log_callback(f"Recipe error detail ({response.status_code}): {detail}")
        return None, f"HTTP_{response.status_code}", None

    return None, (last_error or "RECIPE_ENDPOINT_UNAVAILABLE"), None

# --- MAIN IMAGE TASK WITH RETRY ---
def generate_image_task(session, prompt, index, output_folder, ratio, bearer_token,
                        log_callback, rate_controller, progress_data=None, character_runtime=None,
                        runtime_options=None, stop_event=None):
    """Full image generation with retry logic."""
    alias_map = {}
    used_aliases = []
    reference_inputs = []
    options = runtime_options or {}
    seed_mode = options.get("seed_mode", "random")
    fixed_seed = options.get("fixed_seed")
    consistency_level = _normalize_consistency_level(options.get("consistency_level"))
    generation_mode = normalize_generation_mode(options.get("generation_mode"))
    seed_for_task = _resolve_seed(fixed_seed if seed_mode == "fixed" else None)
    backend_kind = "recipe"
    last_failure_code = None
    phase_marked_generating = False
    phase_marked_upscaling = False

    def _mark_phase(phase, message=""):
        _emit_phase_progress(progress_data, index, phase, message)

    def _emit_skipped():
        _emit_progress(progress_data, index, {
            "status": "skipped",
            "reason": "Stopped by user request.",
            "error_code": SKIPPED_MARKER,
        })

    if stop_event and stop_event.is_set():
        _emit_skipped()
        return SKIPPED_RESULT

    if character_runtime:
        alias_map = character_runtime.get("aliases", {})
        if generation_mode == GENERATION_MODE_FLOW:
            # Flow reference image mode: pass ALL uploaded images directly —
            # no alias-in-prompt matching required (mirrors the real Flow UI UX).
            for entry in alias_map.values():
                for media_id in entry.get("media_ids", []):
                    if media_id and not any(r.get("media_id") == media_id for r in reference_inputs):
                        reference_inputs.append({
                            "media_id": media_id,
                            "alias": entry.get("alias", ""),
                            "caption": "",
                        })
            if reference_inputs:
                log_callback(f"Info [{index}]: using {len(reference_inputs)} Flow reference image(s).")
        elif alias_map:
            # Whisk mode: alias must appear in the prompt text to be included.
            available_aliases = []
            for item in alias_map.values():
                alias_name = item.get("alias")
                if alias_name and alias_name not in available_aliases:
                    available_aliases.append(alias_name)
            used_aliases = _extract_prompt_aliases(prompt, available_aliases)
            if used_aliases:
                missing_aliases = []
                for alias in used_aliases:
                    info = alias_map.get(_alias_key(alias), {})
                    static_ids = [m for m in info.get("media_ids", []) if m]
                    dynamic_ids = [m for m in info.get("dynamic_media_ids", []) if m]
                    if consistency_level == "very_high":
                        selected_ids = static_ids[:MAX_REFERENCE_PER_ALIAS] + dynamic_ids[:MAX_DYNAMIC_ANCHORS_PER_ALIAS]
                    elif consistency_level == "high":
                        selected_ids = static_ids[:MAX_REFERENCE_PER_ALIAS] + dynamic_ids[:1]
                    else:
                        selected_ids = static_ids[:1]

                    # Dedup while preserving order
                    unique_selected = []
                    for media_id in selected_ids:
                        if media_id and media_id not in unique_selected:
                            unique_selected.append(media_id)

                    if not unique_selected:
                        missing_aliases.append(alias)
                        continue

                    for media_id in unique_selected:
                        reference_inputs.append({
                            "media_id": media_id,
                            "alias": alias,
                            "caption": (
                                f"Reference character {alias}. Preserve identity exactly: "
                                "same face, hair, accessories, and body proportions."
                            ),
                        })

                if missing_aliases:
                    log_callback(f"Error [{index}]: missing reference image for alias {', '.join(missing_aliases)}")
                    _emit_progress(progress_data, index, {
                        "status": "fail",
                        "reason": _friendly_failure_reason("MISSING_REFERENCE_ALIAS"),
                        "error_code": "MISSING_REFERENCE_ALIAS",
                    })
                    return False

                log_callback(f"Info [{index}]: using reference alias {', '.join(used_aliases)}")
                if seed_mode == "random" and consistency_level == "very_high":
                    seed_for_task = _seed_from_aliases(used_aliases)

    for attempt in range(MAX_RETRIES):
        if not phase_marked_generating:
            _mark_phase("generating", "Generating image")
            phase_marked_generating = True
        if stop_event and stop_event.is_set():
            _emit_skipped()
            return SKIPPED_RESULT
        delay = rate_controller.wait_if_needed(stop_event=stop_event)
        if not _sleep_with_stop(delay, stop_event):
            _emit_skipped()
            return SKIPPED_RESULT

        if attempt > 0:
            log_callback(f"Retry [{index}] {attempt}/{MAX_RETRIES}")

        if generation_mode == GENERATION_MODE_FLOW:
            backend_kind = "flow_batch"
            image_data, error, gen_meta = generate_image_with_flow_batch(
                prompt,
                ratio,
                bearer_token,
                log_callback,
                session=session,
                seed=seed_for_task,
                runtime_options=options,
                reference_inputs=reference_inputs,
            )
            # Only hard auth failure (401) or missing project ID are truly fatal.
            # 403 reCAPTCHA errors CAN fall back to the workflow endpoint (no reCAPTCHA needed).
            _FLOW_FATAL_ERRORS = {"HTTP_401", "FLOW_PROJECT_ID_MISSING"}
            if error in _FLOW_FATAL_ERRORS:
                log_callback(f"[Flow] Fatal error ({error}) — aborting, no fallback.")
            elif error and error not in {"RATE_LIMIT", "UNSAFE_PROMPT"}:
                log_callback(
                    f"Flow batch fallback [{index}]: {error}. Trying workflow endpoint compatibility mode."
                )
                image_data, error, gen_meta = None, None, None

        if generation_mode != GENERATION_MODE_FLOW or (not image_data and not error):
            if reference_inputs:
                prompt_for_recipe = _build_identity_lock_prompt(
                    prompt,
                    used_aliases,
                    consistency_level=consistency_level,
                )
                image_data, error, gen_meta = generate_image_with_recipe(
                    prompt_for_recipe,
                    ratio,
                    bearer_token,
                    reference_inputs,
                    log_callback,
                    session,
                    seed=seed_for_task,
                    generation_mode=generation_mode,
                )
            else:
                backend_kind = "workflow"
                workflow_id, error = create_workflow(
                    session,
                    prompt,
                    log_callback,
                    generation_mode=generation_mode,
                )

                if error == "UNSAFE_PROMPT":
                    _emit_progress(progress_data, index, {
                        "status": "unsafe",
                        "reason": _friendly_failure_reason(error),
                        "error_code": error,
                    })
                    return UNSAFE_RESULT
                if error == "RATE_LIMIT":
                    rate_controller.on_rate_limit()
                    last_failure_code = error
                    continue
                if error:
                    if stop_event and stop_event.is_set():
                        _emit_skipped()
                        return SKIPPED_RESULT
                    log_callback(f"Workflow error [{index}]: {error}")
                    last_failure_code = error
                    continue
                if not workflow_id:
                    last_failure_code = "WORKFLOW_EMPTY"
                    continue

                image_data, error, gen_meta = generate_image_from_workflow(
                    workflow_id,
                    prompt,
                    ratio,
                    bearer_token,
                    log_callback,
                    session,
                    seed=seed_for_task,
                    generation_mode=generation_mode,
                )

        if error == "RATE_LIMIT":
            rate_controller.on_rate_limit()
            last_failure_code = error
            continue
        if error == "UNSAFE_PROMPT":
            _emit_progress(progress_data, index, {
                "status": "unsafe",
                "reason": _friendly_failure_reason(error),
                "error_code": error,
            })
            return UNSAFE_RESULT
        if error:
            if stop_event and stop_event.is_set():
                _emit_skipped()
                return SKIPPED_RESULT
            log_callback(f"Generate error [{index}]: {error}")
            last_failure_code = error
            continue
        if not image_data:
            last_failure_code = "NO_IMAGE"
            continue

        rate_controller.on_success()

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        if stop_event and stop_event.is_set():
            _emit_skipped()
            return SKIPPED_RESULT

        filename = build_output_filename(output_folder, index, ".jpg")
        raw_output_path = filename
        upscale_applied = False
        upscale_engine_used = ""
        upscale_elapsed_ms = 0
        upscale_error = ""

        upscale_enabled = bool(options.get("upscale_enabled", False)) and callable(upscale_image_file)
        upscale_scale = int(options.get("upscale_scale", 2) or 2)
        upscale_target_preset = str(options.get("upscale_target_preset", "") or "").strip()
        upscale_target_width = int(options.get("upscale_target_width", 0) or 0)
        upscale_target_height = int(options.get("upscale_target_height", 0) or 0)
        upscale_engine = str(options.get("upscale_engine", "auto") or "auto").strip().lower()
        if upscale_engine not in {"auto", "realesrgan", "opencv"}:
            upscale_engine = "auto"
        upscale_mode = str(options.get("upscale_mode", "replace") or "replace").strip().lower()
        keep_raw = bool(options.get("upscale_keep_raw", False)) or upscale_mode == "keep_both"

        if not upscale_enabled:
            with open(filename, "wb") as f:
                f.write(image_data)
        else:
            temp_raw = False
            if keep_raw:
                raw_dir = os.path.join(output_folder, "_raw")
                os.makedirs(raw_dir, exist_ok=True)
                raw_output_path = os.path.join(raw_dir, os.path.basename(filename))
            else:
                fd, raw_output_path = tempfile.mkstemp(
                    prefix=f"aw_raw_{int(index):03d}_",
                    suffix=".jpg",
                    dir=output_folder,
                )
                os.close(fd)
                temp_raw = True

            with open(raw_output_path, "wb") as f:
                f.write(image_data)

            upscale_payload = {
                "bin_path": options.get("upscale_bin_path", ""),
                "model_name": options.get("upscale_model_name", ""),
                "model_dir": options.get("upscale_model_dir", ""),
                "timeout_sec": options.get("upscale_timeout_sec", 300),
                "jpg_quality": options.get("upscale_jpg_quality", 95),
                "png_compression": options.get("upscale_png_compression", 3),
                "target_preset": upscale_target_preset,
                "target_width": upscale_target_width,
                "target_height": upscale_target_height,
                "gpu_id": options.get("upscale_gpu_id", ""),
                "tile_size": options.get("upscale_tile_size", 0),
                "tta_mode": options.get("upscale_tta_mode", False),
                "auto_setup": options.get("upscale_auto_setup", True),
                "verify_checksum": options.get("upscale_verify_checksum", True),
                "setup_dir": options.get("upscale_setup_dir", ""),
                "setup_force_download": options.get("upscale_setup_force_download", False),
                "setup_download_timeout_sec": options.get("upscale_setup_download_timeout_sec", 1200),
            }

            if stop_event and stop_event.is_set():
                if temp_raw:
                    try:
                        os.remove(raw_output_path)
                    except Exception:
                        pass
                _emit_skipped()
                return SKIPPED_RESULT

            if not phase_marked_upscaling:
                _mark_phase("upscaling", "Upscaling image")
                phase_marked_upscaling = True

            upscale_semaphore = options.get("upscale_semaphore")
            if not hasattr(upscale_semaphore, "acquire") or not hasattr(upscale_semaphore, "release"):
                upscale_semaphore = UPSCALE_SEMAPHORE
            acquired = False
            try:
                upscale_semaphore.acquire()
                acquired = True
                upscale_result = upscale_image_file(
                    raw_output_path,
                    filename,
                    scale=upscale_scale,
                    engine=upscale_engine,
                    options=upscale_payload,
                    log_callback=log_callback,
                )
            except Exception as exc:
                upscale_result = {
                    "ok": False,
                    "engine": upscale_engine,
                    "error": str(exc),
                    "elapsed_ms": 0,
                }
            finally:
                if acquired:
                    try:
                        upscale_semaphore.release()
                    except Exception:
                        pass

            upscale_applied = bool(upscale_result.get("ok"))
            upscale_engine_used = str(upscale_result.get("engine", "")).strip()
            upscale_elapsed_ms = int(upscale_result.get("elapsed_ms", 0) or 0)
            upscale_error = str(upscale_result.get("error", "")).strip()
            upscale_target_width = int(upscale_result.get("width", upscale_target_width) or upscale_target_width or 0)
            upscale_target_height = int(upscale_result.get("height", upscale_target_height) or upscale_target_height or 0)
            upscale_target_preset = str(upscale_result.get("target_preset", upscale_target_preset) or upscale_target_preset).strip()

            if not upscale_applied:
                try:
                    if raw_output_path != filename:
                        shutil.copyfile(raw_output_path, filename)
                    elif not os.path.isfile(filename):
                        with open(filename, "wb") as f:
                            f.write(image_data)
                except Exception:
                    with open(filename, "wb") as f:
                        f.write(image_data)
                log_callback(
                    f"⚠️ Upscale failed for [{index}] ({upscale_engine}). "
                    f"Using original image. {upscale_error}"
                )

            if temp_raw:
                try:
                    os.remove(raw_output_path)
                except Exception:
                    pass

        metadata = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "index": index,
            "output_path": os.path.abspath(filename),
            "raw_output_path": os.path.abspath(raw_output_path) if (upscale_enabled and keep_raw) else "",
            "prompt": prompt,
            "seed": (gen_meta or {}).get("seed", seed_for_task),
            "seed_mode": seed_mode,
            "consistency_level": consistency_level,
            "ratio": ratio,
            "backend": backend_kind,
            "generation_mode": generation_mode,
            "used_aliases": used_aliases,
            "reference_media_ids": [item.get("media_id") for item in reference_inputs if item.get("media_id")],
            "workflow_id": (gen_meta or {}).get("workflowId"),
            "output_media_generation_id": (gen_meta or {}).get("mediaGenerationId"),
            "image_model": (gen_meta or {}).get("imageModel"),
            "aspect_ratio": (gen_meta or {}).get("aspectRatio"),
            "upscale_enabled": bool(upscale_enabled),
            "upscale_applied": bool(upscale_applied),
            "upscale_scale": upscale_scale if upscale_enabled else 0,
            "upscale_target_preset": upscale_target_preset if upscale_enabled else "",
            "upscale_target_width": upscale_target_width if upscale_enabled else 0,
            "upscale_target_height": upscale_target_height if upscale_enabled else 0,
            "upscale_engine": upscale_engine_used if upscale_enabled else "",
            "upscale_elapsed_ms": upscale_elapsed_ms if upscale_enabled else 0,
            "upscale_error": upscale_error if upscale_enabled else "",
        }
        _append_metadata_line(runtime_options, metadata, log_callback)

        # Rolling anchor: reuse successful output media as an extra reference in later prompts.
        output_media_id = (gen_meta or {}).get("mediaGenerationId")
        if output_media_id and used_aliases and character_runtime and consistency_level in {"high", "very_high"}:
            runtime_lock = character_runtime.get("lock")
            if runtime_lock:
                with runtime_lock:
                    for alias in used_aliases:
                        info = alias_map.get(_alias_key(alias))
                        if not info:
                            continue
                        static_ids = info.get("media_ids", [])
                        dynamic_ids = info.setdefault("dynamic_media_ids", [])
                        if output_media_id in static_ids or output_media_id in dynamic_ids:
                            continue
                        dynamic_ids.insert(0, output_media_id)
                        del dynamic_ids[MAX_DYNAMIC_ANCHORS_PER_ALIAS:]

        _emit_progress(progress_data, index, {
            "status": "success",
            "path": filename,
        })

        return True

    log_callback(f"Failed [{index}] after {MAX_RETRIES} retries")
    if stop_event and stop_event.is_set():
        _emit_skipped()
        return SKIPPED_RESULT
    _emit_progress(progress_data, index, {
        "status": "fail",
        "reason": _friendly_failure_reason(last_failure_code),
        "error_code": str(last_failure_code or ""),
    })

    return False


# --- UI ENTRY POINT ---
def run_generation_from_ui(prompts, cookie, ratio, threads, output_folder, 
                           log_callback, stop_event, progress_callback=None,
                           cookie_update_callback=None, character_refs=None,
                           seed_mode="random", fixed_seed=None, consistency_level="standard",
                           bearer_token=None, runtime_control=None, upscale_options=None,
                           generation_mode=GENERATION_MODE_WHISK,
                           flow_project_id="", flow_session_cookie="",
                           flow_model_name="BANANA_PRO_2"):
    """Main function with adaptive rate limiting"""
    
    num_prompts = len(prompts)
    suggested_threads, est_time = suggest_threads(num_prompts)
    effective_mode = normalize_generation_mode(generation_mode)
    mode_profile = _mode_profile(effective_mode)
    
    log_callback(f"🚀 Starting generation!")
    
    # Use provided cookie or fallback
    final_cookie = cookie if cookie else HARDCODED_COOKIE
    cookie_holder = {"value": final_cookie}
    
    
    # Choose Backend
    pw_client = None
    session = None
    
    # METHOD 1: Playwright (DISABLED by user request)
    USE_PLAYWRIGHT = False 
    
    # Fallback to Python/Rust if Playwright disabled (Legacy)
    if not pw_client:
         log_callback("🐍 Using Python Backend (curl_cffi).")
         session = build_runtime_session(cookie_holder["value"], generation_mode=effective_mode)
         
         def _handle_cookie_refresh(new_cookie):
             fresh = str(new_cookie or "").strip()
             if not fresh:
                 return
             previous = str(cookie_holder.get("value") or "").strip()
             cookie_holder["value"] = fresh
             if session:
                 # Only update session cookie header with a real cookie string,
                 # never with a bearer token value.
                 if "=" in fresh and not fresh.startswith("ya29."):
                     session.headers["cookie"] = fresh
                 elif "cookie" in session.headers:
                     del session.headers["cookie"]
             if cookie_update_callback and fresh != previous:
                 cookie_update_callback(fresh)
                 log_callback("🔄 Access key refreshed in runtime.")
         # For Flow mode: if a session cookie is provided, exchange it for a fresh bearer token
         # (This takes priority over an already-cached bearer token from the access key check)
         if (effective_mode == GENERATION_MODE_FLOW
                 and flow_session_cookie
                 and not _looks_like_bearer_token(flow_session_cookie)):
             log_callback("🍪 Flow Session Cookie provided — exchanging for Bearer Token...")
             cookie_token = get_bearer_token_from_session(
                 flow_session_cookie,
                 log_callback,
                 on_cookie_refresh=_handle_cookie_refresh,
                 generation_mode=GENERATION_MODE_FLOW,
             )
             if cookie_token:
                 bearer_token = cookie_token
                 log_callback("✅ Fresh Bearer Token obtained from session cookie.")
             else:
                 log_callback("⚠️ Session cookie exchange failed — falling back to Access Key...")
             # Set session cookie so workflow fallback endpoints (labs.google/trpc) can auth
             session.headers["cookie"] = flow_session_cookie
         if not bearer_token:
             log_callback("🔐 Fetching Bearer Token...")
             bearer_token = get_bearer_token_from_session(
                 cookie_holder["value"],
                 log_callback,
                 on_cookie_refresh=_handle_cookie_refresh,
                 generation_mode=effective_mode,
             )
             if not bearer_token:
                 log_callback("❌ Failed to get Bearer Token. Access key may be expired!")
                 return 0, 0
         if cookie_holder["value"] != session.headers.get("cookie"):
             fresh = cookie_holder["value"]
             # Bearer tokens (ya29.) must NOT go into the Cookie header; they belong
             # in the Authorization header only. Skip setting cookie for Flow bearer tokens.
             if not (effective_mode == GENERATION_MODE_FLOW and fresh.startswith("ya29.")):
                 session.headers["cookie"] = fresh
         if runtime_control:
             with runtime_control["lock"]:
                 runtime_control["session"] = session
                 runtime_control["active"] = True

    if stop_event.is_set():
        if runtime_control:
            with runtime_control["lock"]:
                runtime_control["active"] = False
                runtime_control["session"] = None
        return 0, 0

    character_runtime = prepare_character_registry(session, character_refs or [], log_callback)
    runtime_options = _build_runtime_options(
        seed_mode=seed_mode,
        fixed_seed=fixed_seed,
        consistency_level=consistency_level,
        upscale_options=upscale_options,
        generation_mode=effective_mode,
        flow_project_id=flow_project_id,
        flow_session_cookie=flow_session_cookie,
        flow_model_name=flow_model_name,
    )
    os.makedirs(output_folder, exist_ok=True)

    effective_threads = int(threads)
    if runtime_options["consistency_level"] == "very_high" and character_refs:
        if effective_threads > 1:
            log_callback("🧷 Consistency=very_high: forcing single-thread mode for better character stability.")
        effective_threads = 1

    if runtime_options.get("upscale_enabled"):
        # Scale upscale workers dynamically with image count, up to 20
        # Each image gets its own upscale worker — no queuing bottleneck
        upscale_workers = max(1, min(num_prompts, 20))
        runtime_options["upscale_workers"] = upscale_workers
        runtime_options["upscale_semaphore"] = th.Semaphore(upscale_workers)

        # Pre-warm RealESRGAN in background so binary/models are ready
        # before the first image even finishes generating
        if runtime_options.get("upscale_engine", "auto") in {"auto", "realesrgan"}:
            _prewarm_opts = {
                "auto_setup": runtime_options.get("upscale_auto_setup", True),
                "verify_checksum": runtime_options.get("upscale_verify_checksum", True),
                "setup_dir": runtime_options.get("upscale_setup_dir", ""),
                "bin_path": runtime_options.get("upscale_bin_path", ""),
                "model_dir": runtime_options.get("upscale_model_dir", ""),
                "setup_download_timeout_sec": runtime_options.get("upscale_setup_download_timeout_sec", 1200),
            }
            _prewarm_t = th.Thread(
                target=pre_warm_realesrgan,
                kwargs={"options": _prewarm_opts, "log_callback": log_callback},
                daemon=True,
            )
            _prewarm_t.start()

    log_callback(f"📊 Prompts: {num_prompts} | Threads: {effective_threads} | Ratio: {ratio}")
    log_callback(f"🧠 Engine: {mode_profile['label']}")
    if runtime_options["seed_mode"] == "fixed":
        log_callback(f"🎯 Seed mode: fixed ({runtime_options['fixed_seed']})")
    else:
        log_callback("🎲 Seed mode: random")
    log_callback(f"🧷 Consistency level: {runtime_options['consistency_level']}")
    if runtime_options.get("upscale_enabled"):
        upscale_target = str(runtime_options.get("upscale_target_preset", "") or "").strip()
        if upscale_target:
            target_summary = (
                f"target={upscale_target} "
                f"({runtime_options.get('upscale_target_width', 0)}x{runtime_options.get('upscale_target_height', 0)})"
            )
        else:
            target_summary = f"x{runtime_options.get('upscale_scale', 2)}"
        log_callback(
            "🆙 Upscale: ON "
            f"({runtime_options.get('upscale_engine', 'auto')} {target_summary}, "
            f"mode={runtime_options.get('upscale_mode', 'replace')}, workers={runtime_options.get('upscale_workers', _UPSCALE_MAX_WORKERS)})"
        )
        if runtime_options.get("upscale_engine") in {"auto", "realesrgan"}:
            log_callback(
                "📦 RealESRGAN auto-setup: "
                f"{'ON' if runtime_options.get('upscale_auto_setup', True) else 'OFF'} | "
                f"checksum: {'ON' if runtime_options.get('upscale_verify_checksum', True) else 'OFF'}"
            )
    else:
        log_callback("🆙 Upscale: OFF")
    log_callback(f"📂 Output: {output_folder}")

    # Rate limit controller
    rate_controller = RateLimitController(log_callback)
    
    # Progress tracking
    progress_data = {
        'lock': th.Lock(),
        'completed': 0,
        'total': num_prompts,
        'callback': progress_callback,
        'reported': set(),
    }

    def _is_reported(index):
        with progress_data["lock"]:
            return index in progress_data.setdefault("reported", set())
    
    # Track results
    counts = {"success": 0, "fail": 0, "skipped": 0, "unsafe": 0}
    count_lock = th.Lock()
    start_ts = time.time()
    heartbeat_every = max(25, min(200, (num_prompts // 20) if num_prompts else 25))
    heartbeat_interval_sec = 15.0
    telemetry_lock = th.Lock()
    telemetry_state = {
        "last_logged_completed": 0,
        "last_log_time": start_ts,
    }

    def _snapshot_counts():
        with count_lock:
            return dict(counts)

    def _completed_total(snapshot):
        return int(snapshot.get("success", 0)) + int(snapshot.get("fail", 0)) + int(snapshot.get("unsafe", 0)) + int(snapshot.get("skipped", 0))

    def _maybe_log_heartbeat(force=False):
        snapshot = _snapshot_counts()
        done = _completed_total(snapshot)
        now = time.time()
        with telemetry_lock:
            last_done = telemetry_state["last_logged_completed"]
            last_time = telemetry_state["last_log_time"]
            should_log = force or (done >= num_prompts) or (done > 0 and (done - last_done) >= heartbeat_every) or (done > 0 and (now - last_time) >= heartbeat_interval_sec)
            if not should_log:
                return
            telemetry_state["last_logged_completed"] = done
            telemetry_state["last_log_time"] = now

        elapsed = max(0.001, now - start_ts)
        throughput_per_min = (done / elapsed) * 60.0
        remaining = max(0, num_prompts - done)
        eta_min = (remaining / (done / elapsed) / 60.0) if done > 0 else 0.0
        rl_status = rate_controller.get_status()
        log_callback(
            "⏱️ Progress {done}/{total} | throughput {rate:.2f} img/min | ETA {eta:.1f} min | "
            "delay={delay}s | 429={total_429}".format(
                done=done,
                total=num_prompts,
                rate=throughput_per_min,
                eta=eta_min,
                delay=rl_status.get("delay", 0),
                total_429=rl_status.get("total_429", 0),
            )
        )

    def _mark_skipped(index):
        with count_lock:
            counts["skipped"] += 1
        _emit_progress(progress_data, index, {
            "status": "skipped",
            "reason": "Stopped by user request.",
            "error_code": SKIPPED_MARKER,
        })
        _maybe_log_heartbeat()

    task_queue = queue.Queue()
    for i, prompt in enumerate(prompts, 1):
        task_queue.put((i, prompt))

    def worker():
        local_session = build_runtime_session(cookie_holder["value"], generation_mode=effective_mode)
        try:
            while True:
                if stop_event.is_set():
                    return
                try:
                    index, prompt = task_queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    if stop_event.is_set():
                        _mark_skipped(index)
                        return
                    try:
                        result = generate_image_task(
                            local_session, prompt, index, output_folder, ratio, bearer_token,
                            log_callback, rate_controller, progress_data, character_runtime,
                            runtime_options=runtime_options,
                            stop_event=stop_event,
                        )
                    except Exception as exc:
                        if stop_event.is_set():
                            _emit_progress(progress_data, index, {
                                "status": "skipped",
                                "reason": "Stopped by user request.",
                                "error_code": SKIPPED_MARKER,
                            })
                            result = SKIPPED_RESULT
                        else:
                            log_callback(f"❌ [{index}] Worker exception: {exc}")
                            _emit_progress(progress_data, index, {
                                "status": "fail",
                                "reason": _friendly_failure_reason("WORKER_EXCEPTION"),
                                "error_code": "WORKER_EXCEPTION",
                            })
                            result = False
                    if result is False and not _is_reported(index):
                        _emit_progress(progress_data, index, {
                            "status": "fail",
                            "reason": _friendly_failure_reason("UNREPORTED_FAILURE"),
                            "error_code": "UNREPORTED_FAILURE",
                        })
                    with count_lock:
                        if result is True:
                            counts["success"] += 1
                        elif result == UNSAFE_RESULT:
                            counts["unsafe"] += 1
                        elif result == SKIPPED_RESULT:
                            counts["skipped"] += 1
                        else:
                            counts["fail"] += 1
                    _maybe_log_heartbeat()
                finally:
                    task_queue.task_done()
        finally:
            try:
                local_session.close()
            except Exception:
                pass

    worker_count = min(effective_threads, num_prompts) if num_prompts > 0 else 0
    if worker_count > 0:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(worker) for _ in range(worker_count)]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    log_callback(f"⚠️ Worker crashed: {exc}")

    if stop_event.is_set():
        remaining = []
        while True:
            try:
                index, _prompt = task_queue.get_nowait()
            except queue.Empty:
                break
            remaining.append(index)
            task_queue.task_done()
        if remaining:
            log_callback(f"🛑 Stopped by user. Skipping {len(remaining)} pending tasks.")
            for index in remaining:
                _mark_skipped(index)
    else:
        missing_reports = [i for i in range(1, num_prompts + 1) if not _is_reported(i)]
        if missing_reports:
            log_callback(f"⚠️ Missing result for {len(missing_reports)} item(s). Marking them as failed.")
            for index in missing_reports:
                _emit_progress(progress_data, index, {
                    "status": "fail",
                    "reason": _friendly_failure_reason("MISSING_RESULT"),
                    "error_code": "MISSING_RESULT",
                })
            with count_lock:
                counts["fail"] += len(missing_reports)

    _maybe_log_heartbeat(force=True)
    
    # Summary
    success_count = counts["success"]
    fail_count = counts["fail"]
    skipped_count = counts["skipped"]
    unsafe_count = counts["unsafe"]
    processed = success_count + fail_count + unsafe_count
    log_callback("=" * 50)
    log_callback(f"✅ Success: {success_count}/{num_prompts}")
    log_callback(f"❌ Failed: {fail_count}/{num_prompts}")
    if skipped_count:
        log_callback(f"🛑 Skipped: {skipped_count}/{num_prompts}")
    if unsafe_count:
        log_callback(f"🚫 Unsafe: {unsafe_count}/{num_prompts}")
    if processed:
        log_callback(f"📈 Success rate: {round(success_count/processed*100, 1)}% (processed)")
    else:
        log_callback("📈 Success rate: 0% (processed)")
    log_callback("=" * 50)
    if runtime_control:
        with runtime_control["lock"]:
            runtime_control["active"] = False
            runtime_control["session"] = None
    
    return success_count, fail_count

# --- CLI MAIN ---
def main():
    # ... (Keep existing CLI logic but ensure imports)
    import argparse
    import sys
    # ... (rest of main is mostly UI wrapper call)
    
    parser = argparse.ArgumentParser(description='Generate image from prompt')
    parser.add_argument('--prompt', required=False, help='Single prompt (deprecated)')
    parser.add_argument('--prompts_json', required=False, help='JSON string')
    parser.add_argument('--cookie', required=False, help='Cookie')
    parser.add_argument('--output', required=False, default='./OUTPUT', help='Output folder')
    parser.add_argument('--ratio', required=False, default='1:1', help='Aspect ratio')
    parser.add_argument('--threads', required=False, type=int, default=1, help='Number of threads')
    parser.add_argument('--seed_mode', required=False, default='random', choices=['random', 'fixed'],
                        help='Seed mode: random or fixed')
    parser.add_argument('--seed', required=False, type=int, help='Fixed seed value (when seed_mode=fixed)')
    parser.add_argument(
        '--consistency_level',
        required=False,
        default='standard',
        choices=['standard', 'high', 'very_high'],
        help='Character consistency level',
    )
    parser.add_argument(
        '--upscale',
        dest='upscale',
        action=argparse.BooleanOptionalAction,
        default=None,
        help='Enable post-generation upscale.',
    )
    parser.add_argument(
        '--upscale_scale',
        required=False,
        type=int,
        choices=[2, 3, 4],
        default=None,
        help='Upscale factor for post-processing.',
    )
    parser.add_argument(
        '--upscale_target',
        required=False,
        choices=['1080p', '2K', '4K'],
        default=None,
        help='Upscale target preset that fits output inside a standard resolution box.',
    )
    parser.add_argument(
        '--upscale_engine',
        required=False,
        choices=['auto', 'realesrgan', 'opencv'],
        default=None,
        help='Upscale backend engine.',
    )
    parser.add_argument(
        '--upscale_mode',
        required=False,
        choices=['replace', 'keep_both'],
        default=None,
        help='Upscale output mode.',
    )
    parser.add_argument(
        '--upscale_keep_raw',
        dest='upscale_keep_raw',
        action=argparse.BooleanOptionalAction,
        default=None,
        help='Keep raw pre-upscale image files.',
    )
    parser.add_argument('--upscale_bin', required=False, default=None, help='Path to realesrgan binary.')
    parser.add_argument('--upscale_model', required=False, default=None, help='Realesrgan model name.')
    parser.add_argument('--upscale_model_dir', required=False, default=None, help='Realesrgan model directory.')
    parser.add_argument(
        '--upscale_auto_setup',
        dest='upscale_auto_setup',
        action=argparse.BooleanOptionalAction,
        default=None,
        help='Auto download/setup RealESRGAN package when needed.',
    )
    parser.add_argument(
        '--upscale_verify_checksum',
        dest='upscale_verify_checksum',
        action=argparse.BooleanOptionalAction,
        default=None,
        help='Verify SHA256 checksum for downloaded RealESRGAN package.',
    )
    parser.add_argument(
        '--upscale_setup_force_download',
        dest='upscale_setup_force_download',
        action=argparse.BooleanOptionalAction,
        default=None,
        help='Force re-download of RealESRGAN package.',
    )
    parser.add_argument('--upscale_setup_dir', required=False, default=None, help='Directory for auto setup assets.')
    parser.add_argument(
        '--upscale_setup_timeout',
        required=False,
        type=int,
        default=None,
        help='Download timeout seconds for RealESRGAN auto setup.',
    )
    parser.add_argument(
        '--generation_mode',
        required=False,
        default=GENERATION_MODE_WHISK,
        choices=[GENERATION_MODE_WHISK, GENERATION_MODE_FLOW],
        help='Generation engine mode',
    )
    parser.add_argument('--flow_project_id', required=False, default="", help='Google Flow project ID')
    parser.add_argument('--flow_session_cookie', required=False, default="", help='Google Flow session cookie for Bearer token exchange')
    parser.add_argument('--flow_model_name', required=False, default="BANANA_PRO_2", help='Google Flow model name')

    args = parser.parse_args()
    
    def log_callback(msg):
        try:
            if msg.startswith('{'): print(msg, flush=True)
            else: print(json.dumps({"type": "log", "msg": msg}), flush=True)
        except: print(msg, flush=True)

    def progress_callback(completed, total, image_path, index):
        if isinstance(image_path, dict):
            status = str(image_path.get("status", "")).strip().lower()
            reason = str(image_path.get("reason", "")).strip()
            path = image_path.get("path")
            if status == "phase":
                return
            if status == "success" and path:
                print(json.dumps({
                    "type": "result", "index": index, "success": True, "image_path": path
                }), flush=True)
            else:
                print(json.dumps({
                    "type": "result",
                    "index": index,
                    "success": False,
                    "message": reason or ("Unsafe prompt" if status == "unsafe" else "Failed generation"),
                }), flush=True)
        elif image_path == SKIPPED_MARKER:
            print(json.dumps({
                "type": "result", "index": index, "success": False, "message": "Skipped"
            }), flush=True)
        elif image_path == UNSAFE_MARKER:
            print(json.dumps({
                "type": "result", "index": index, "success": False, "message": "Unsafe prompt"
            }), flush=True)
        elif image_path:
            print(json.dumps({
                "type": "result", "index": index, "success": True, "image_path": image_path
            }), flush=True)
        else:
            print(json.dumps({
                "type": "result", "index": index, "success": False, "message": "Failed generation"
            }), flush=True)

    stop_event = th.Event()
    cookie = args.cookie if args.cookie else HARDCODED_COOKIE
    output_folder = args.output
    os.makedirs(output_folder, exist_ok=True)

    prompts_list = []
    if args.prompts_json:
        try:
            data = json.loads(args.prompts_json)
            if isinstance(data, list):
                if data and isinstance(data[0], dict) and 'prompt' in data[0]:
                     prompts_list = [item['prompt'] for item in data]
                else:
                     prompts_list = data
        except Exception as e:
            sys.exit(1)
    elif args.prompt:
        prompts_list = [args.prompt]
    
    if not prompts_list:
        sys.exit(1)

    upscale_options = {}
    if args.upscale is not None:
        upscale_options["enabled"] = bool(args.upscale)
    if args.upscale_scale is not None:
        upscale_options["scale"] = int(args.upscale_scale)
    if args.upscale_target is not None:
        upscale_options["target_preset"] = str(args.upscale_target)
    if args.upscale_engine is not None:
        upscale_options["engine"] = str(args.upscale_engine)
    if args.upscale_mode is not None:
        upscale_options["mode"] = str(args.upscale_mode)
    if args.upscale_keep_raw is not None:
        upscale_options["keep_raw"] = bool(args.upscale_keep_raw)
    if args.upscale_bin is not None:
        upscale_options["bin_path"] = str(args.upscale_bin).strip()
    if args.upscale_model is not None:
        upscale_options["model_name"] = str(args.upscale_model).strip()
    if args.upscale_model_dir is not None:
        upscale_options["model_dir"] = str(args.upscale_model_dir).strip()
    if args.upscale_auto_setup is not None:
        upscale_options["auto_setup"] = bool(args.upscale_auto_setup)
    if args.upscale_verify_checksum is not None:
        upscale_options["verify_checksum"] = bool(args.upscale_verify_checksum)
    if args.upscale_setup_force_download is not None:
        upscale_options["setup_force_download"] = bool(args.upscale_setup_force_download)
    if args.upscale_setup_dir is not None:
        upscale_options["setup_dir"] = str(args.upscale_setup_dir).strip()
    if args.upscale_setup_timeout is not None:
        upscale_options["setup_download_timeout_sec"] = int(args.upscale_setup_timeout)

    try:
        run_generation_from_ui(
            prompts_list, cookie, args.ratio, args.threads,
            output_folder, log_callback, stop_event, progress_callback,
            seed_mode=args.seed_mode, fixed_seed=args.seed,
            consistency_level=args.consistency_level,
            upscale_options=upscale_options or None,
            generation_mode=args.generation_mode,
            flow_project_id=args.flow_project_id,
            flow_session_cookie=args.flow_session_cookie,
            flow_model_name=args.flow_model_name,
        )
        print(json.dumps({"type": "done", "success": True}), flush=True)
    except Exception as e:
        print(json.dumps({"type": "done", "success": False, "message": str(e)}), flush=True)
        sys.exit(1)


# --- ASYNC GENERATION ENTRY POINT ---
def run_generation_async(
    prompts, cookie, ratio, threads, output_folder,
    log_callback=None, stop_event=None, progress_callback=None,
    cookie_update_callback=None, character_refs=None,
    seed_mode="random", fixed_seed=None, consistency_level="standard",
    bearer_token=None, upscale_options=None,
    generation_mode=GENERATION_MODE_WHISK,
    flow_project_id="", flow_session_cookie="", flow_model_name="BANANA_PRO_2"
):
    """
    Async version of generation with connection pooling.
    Falls back to sync version if async dependencies not available.
    """
    log_callback = log_callback or (lambda x: None)
    
    # Check if we should use async
    use_async = ASYNC_AVAILABLE and not character_refs and normalize_generation_mode(generation_mode) == GENERATION_MODE_WHISK
    requested_upscale = bool((upscale_options or {}).get("enabled")) if isinstance(upscale_options, dict) else False
    if not requested_upscale:
        requested_upscale = bool((UPSCALE_DEFAULTS or {}).get("enabled", False))
    if requested_upscale and use_async:
        log_callback("⚠️ Async path does not support post-upscale yet, falling back to sync.")
        use_async = False
    
    if not use_async:
        # Fall back to sync version
        return run_generation_from_ui(
            prompts, cookie, ratio, threads, output_folder,
            log_callback, stop_event, progress_callback,
            cookie_update_callback, character_refs,
            seed_mode, fixed_seed, consistency_level, bearer_token,
            upscale_options=upscale_options,
            generation_mode=generation_mode,
            flow_project_id=flow_project_id,
            flow_session_cookie=flow_session_cookie,
            flow_model_name=flow_model_name,
        )

    # Use async version
    try:
        from async_client import run_async_generation
        import asyncio
        
        log_callback("🚀 Using async generation (connection pooling)")
        
        # Run async event loop
        result = asyncio.run(run_async_generation(
            prompts=prompts,
            cookie=cookie,
            ratio=ratio,
            threads=threads,
            output_folder=output_folder,
            log_callback=log_callback,
            progress_callback=progress_callback,
            stop_event=stop_event,
            cookie_update_callback=cookie_update_callback,
            seed_mode=seed_mode,
            fixed_seed=fixed_seed
        ))
        return result
        
    except Exception as e:
        log_callback(f"⚠️ Async generation failed ({e}), falling back to sync")
        return run_generation_from_ui(
            prompts, cookie, ratio, threads, output_folder,
            log_callback, stop_event, progress_callback,
            cookie_update_callback, character_refs,
            seed_mode, fixed_seed, consistency_level, bearer_token,
            upscale_options=upscale_options,
            generation_mode=generation_mode,
            flow_project_id=flow_project_id,
            flow_session_cookie=flow_session_cookie,
            flow_model_name=flow_model_name,
        )


if __name__ == "__main__":
    main()
