import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import httpx


_REALESRGAN_RELEASE = {
    "version": "v0.2.5.0",
    "assets": {
        "ubuntu": {
            "name": "realesrgan-ncnn-vulkan-20220424-ubuntu.zip",
            "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip",
            "sha256": "e5aa6eb131234b87c0c51f82b89390f5e3e642b7b70f2b9bbe95b6a285a40c96",
            "binary": "realesrgan-ncnn-vulkan",
        },
        "macos": {
            "name": "realesrgan-ncnn-vulkan-20220424-macos.zip",
            "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-macos.zip",
            "sha256": "e0ad05580abfeb25f8d8fb55aaf7bedf552c375b5b4d9bd3c8d59764d2cc333a",
            "binary": "realesrgan-ncnn-vulkan",
        },
        "windows": {
            "name": "realesrgan-ncnn-vulkan-20220424-windows.zip",
            "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip",
            "sha256": "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d",
            "binary": "realesrgan-ncnn-vulkan.exe",
        },
    },
}

_AUTO_SETUP_LOCK = threading.Lock()

# ── Speed caches ─────────────────────────────────────────────────────────────
# Cached after first binary discovery → skip file-system scan on every image
_BINARY_CACHE: str = ""
_BINARY_CACHE_LOCK = threading.Lock()

# Cached after first ensure_realesrgan_assets → skip re-setup on every image
_SETUP_CACHE: dict = {}
_SETUP_CACHE_LOCK = threading.Lock()
# ─────────────────────────────────────────────────────────────────────────────


def _env_bool(name, default=False):
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name, default, min_value=None, max_value=None):
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = int(default)
    if min_value is not None and value < min_value:
        value = min_value
    if max_value is not None and value > max_value:
        value = max_value
    return value


def _env_str(name, default=""):
    return str(os.getenv(name, default) or "").strip()


def _clean_engine(raw):
    value = str(raw or "auto").strip().lower()
    if value in {"auto", "realesrgan", "opencv"}:
        return value
    return "auto"


def _clean_mode(raw):
    value = str(raw or "replace").strip().lower()
    if value in {"replace", "keep_both"}:
        return value
    return "replace"


def _clean_target_preset(raw):
    value = str(raw or "").strip().lower()
    if value in {"1080", "1080p", "fhd", "fullhd", "full hd"}:
        return "1080p"
    if value in {"2k", "1440", "1440p", "qhd"}:
        return "2K"
    if value in {"4k", "2160", "2160p", "uhd"}:
        return "4K"
    return ""


def _preset_dimensions(raw):
    preset = _clean_target_preset(raw)
    if preset == "1080p":
        return 1920, 1080
    if preset == "2K":
        return 2560, 1440
    if preset == "4K":
        return 3840, 2160
    return 0, 0


def _platform_key():
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "ubuntu"
    return ""


def _default_setup_root():
    # This file lives at autocapcut/services/upscale_engine.py
    # Two levels up (.., ..) reaches the project root
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(root, "tools", "upscale", "realesrgan")


def _paths_for_setup(setup_root):
    platform_key = _platform_key()
    platform_asset = (_REALESRGAN_RELEASE.get("assets") or {}).get(platform_key, {})
    binary_name = platform_asset.get("binary", "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan")
    return {
        "setup_root": setup_root,
        "downloads_dir": os.path.join(setup_root, "downloads"),
        "bin_dir": os.path.join(setup_root, "bin", platform_key or "unknown"),
        "models_dir": os.path.join(setup_root, "models"),
        "state_file": os.path.join(setup_root, "install_state.json"),
        "binary_path": os.path.join(setup_root, "bin", platform_key or "unknown", binary_name),
        "platform_key": platform_key,
    }


def get_default_upscale_options():
    return {
        "enabled": _env_bool("AUTO_WHISK_UPSCALE_ENABLE", False),
        "scale": _env_int("AUTO_WHISK_UPSCALE_SCALE", 2, 1, 8),
        "target_preset": _clean_target_preset(os.getenv("AUTO_WHISK_UPSCALE_TARGET_PRESET", "")),
        "target_width": _env_int("AUTO_WHISK_UPSCALE_TARGET_WIDTH", 0, 0, 16384),
        "target_height": _env_int("AUTO_WHISK_UPSCALE_TARGET_HEIGHT", 0, 0, 16384),
        "engine": _clean_engine(os.getenv("AUTO_WHISK_UPSCALE_ENGINE", "auto")),
        "mode": _clean_mode(os.getenv("AUTO_WHISK_UPSCALE_MODE", "replace")),
        "keep_raw": _env_bool("AUTO_WHISK_UPSCALE_KEEP_RAW", False),
        "bin_path": _env_str("AUTO_WHISK_UPSCALE_BIN", ""),
        "model_name": _env_str("AUTO_WHISK_UPSCALE_MODEL", ""),
        "model_dir": _env_str("AUTO_WHISK_UPSCALE_MODEL_DIR", ""),
        "timeout_sec": _env_int("AUTO_WHISK_UPSCALE_TIMEOUT", 300, 10, 3600),
        "jpg_quality": _env_int("AUTO_WHISK_UPSCALE_JPG_QUALITY", 95, 60, 100),
        "png_compression": _env_int("AUTO_WHISK_UPSCALE_PNG_COMPRESSION", 3, 0, 9),
        "max_workers": _env_int("AUTO_WHISK_UPSCALE_MAX_WORKERS", 1, 1, 16),
        "gpu_id": _env_str("AUTO_WHISK_UPSCALE_GPU_ID", ""),
        "tile_size": _env_int("AUTO_WHISK_UPSCALE_TILE_SIZE", 0, 0, 8192),
        "tta_mode": _env_bool("AUTO_WHISK_UPSCALE_TTA", False),
        "auto_setup": _env_bool("AUTO_WHISK_UPSCALE_AUTO_SETUP", True),
        "verify_checksum": _env_bool("AUTO_WHISK_UPSCALE_VERIFY_CHECKSUM", True),
        "setup_dir": _env_str("AUTO_WHISK_UPSCALE_SETUP_DIR", _default_setup_root()),
        "setup_force_download": _env_bool("AUTO_WHISK_UPSCALE_SETUP_FORCE_DOWNLOAD", False),
        "setup_download_timeout_sec": _env_int("AUTO_WHISK_UPSCALE_SETUP_TIMEOUT", 1200, 30, 7200),
    }


def _candidate_binaries():
    names = ["realesrgan-ncnn-vulkan", "upscayl-bin"]
    if os.name == "nt":
        names = [f"{name}.exe" for name in names]
    paths = []
    # This file lives at autocapcut/services/upscale_engine.py
    # Two levels up (.., ..) reaches the project root
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    setup_root = _default_setup_root()
    plat = _platform_key() or "unknown"
    for name in names:
        paths.append(os.path.join(setup_root, "bin", plat, name))
        paths.append(os.path.join(root, "tools", "upscale", name))
        paths.append(os.path.join(root, "bin", name))
        paths.append(os.path.join(root, "resources", "upscale", name))
    for name in names:
        paths.append(name)
    return paths


def _is_binary_ready(path):
    if not path or not os.path.isfile(path):
        return False
    if os.name == "nt":
        return True
    return os.access(path, os.X_OK)


def _find_binary(explicit_path):
    global _BINARY_CACHE
    # Fast path: return cached binary if still valid
    with _BINARY_CACHE_LOCK:
        if _BINARY_CACHE and _is_binary_ready(_BINARY_CACHE):
            return _BINARY_CACHE

    value = str(explicit_path or "").strip()
    if value and _is_binary_ready(value):
        with _BINARY_CACHE_LOCK:
            _BINARY_CACHE = value
        return value

    for item in _candidate_binaries():
        found = ""
        if os.path.isabs(item):
            if _is_binary_ready(item):
                found = item
        else:
            resolved = shutil_which(item)
            if resolved and _is_binary_ready(resolved):
                found = resolved
        if found:
            with _BINARY_CACHE_LOCK:
                _BINARY_CACHE = found
            return found
    return ""


def _looks_like_model_dir(path):
    if not path or not os.path.isdir(path):
        return False
    has_param = False
    has_bin = False
    try:
        for name in os.listdir(path):
            lower = name.lower()
            if lower.endswith(".param"):
                has_param = True
            elif lower.endswith(".bin"):
                has_bin = True
            if has_param and has_bin:
                return True
    except Exception:
        return False
    return False


def _resolve_model_dir(explicit_path, setup_root):
    candidate = str(explicit_path or "").strip()
    if _looks_like_model_dir(candidate):
        return candidate

    paths = []
    if setup_root:
        paths.append(os.path.join(setup_root, "models"))
    # This file lives at autocapcut/services/upscale_engine.py
    # Two levels up (.., ..) reaches the project root
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    paths.append(os.path.join(root, "tools", "upscale", "models"))
    paths.append(os.path.join(root, "resources", "upscale", "models"))

    for path in paths:
        if _looks_like_model_dir(path):
            return path
    return candidate if candidate else ""


def shutil_which(cmd):
    try:
        from shutil import which

        return which(cmd)
    except Exception:
        return None


def _sha256_file(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def _download_file(url, out_path, timeout_sec=1200):
    part_path = f"{out_path}.part"
    if os.path.exists(part_path):
        try:
            os.remove(part_path)
        except Exception:
            pass
    with httpx.stream(
        "GET",
        url,
        headers={"User-Agent": "MasterOS-Upgrader/1.0"},
        timeout=max(30, int(timeout_sec)),
        follow_redirects=True,
    ) as response:
        response.raise_for_status()
        with open(part_path, "wb") as f:
            for chunk in response.iter_bytes(1024 * 1024):
                f.write(chunk)
    os.replace(part_path, out_path)


def _safe_extract_zip(zip_path, dest_dir):
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            raw = str(member.filename or "").replace("\\", "/")
            if not raw:
                continue
            if raw.startswith("/") or raw.startswith("../") or "/../" in raw:
                raise RuntimeError(f"Unsafe archive path: {raw}")
            out_path = os.path.realpath(os.path.join(dest_dir, raw))
            base = os.path.realpath(dest_dir)
            if not (out_path == base or out_path.startswith(base + os.sep)):
                raise RuntimeError(f"Archive path escape blocked: {raw}")
        zf.extractall(dest_dir)


def _discover_binary(root_dir):
    names = ["realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan"]
    for folder, _, files in os.walk(root_dir):
        lower_to_name = {name.lower(): name for name in files}
        for name in names:
            real_name = lower_to_name.get(name.lower())
            if not real_name:
                continue
            candidate = os.path.join(folder, real_name)
            if os.path.isfile(candidate):
                return candidate
    return ""


def _discover_models_dir(root_dir):
    for folder, _, _ in os.walk(root_dir):
        if _looks_like_model_dir(folder):
            return folder
    return ""


def _sync_model_dir(source_dir, target_dir):
    os.makedirs(target_dir, exist_ok=True)
    for name in os.listdir(target_dir):
        lower = name.lower()
        if lower.endswith(".bin") or lower.endswith(".param"):
            try:
                os.remove(os.path.join(target_dir, name))
            except Exception:
                pass

    for name in os.listdir(source_dir):
        lower = name.lower()
        if not (lower.endswith(".bin") or lower.endswith(".param")):
            continue
        shutil.copy2(os.path.join(source_dir, name), os.path.join(target_dir, name))


def ensure_realesrgan_assets(options=None, log_callback=None):
    opts = dict(options or {})
    auto_setup = bool(opts.get("auto_setup", True))
    if not auto_setup:
        return {"ok": False, "error": "auto setup disabled", "binary_path": "", "model_dir": ""}

    def _log(msg):
        if log_callback:
            log_callback(str(msg))

    setup_root = str(opts.get("setup_dir") or _default_setup_root()).strip()
    paths = _paths_for_setup(setup_root)
    platform_key = paths["platform_key"]
    platform_asset = (_REALESRGAN_RELEASE.get("assets") or {}).get(platform_key)
    if not platform_asset:
        return {"ok": False, "error": f"unsupported platform for auto setup: {sys.platform}", "binary_path": "", "model_dir": ""}

    expected_sha = str(platform_asset.get("sha256") or "").strip().lower()
    archive_name = platform_asset.get("name")
    archive_url = platform_asset.get("url")
    binary_target = paths["binary_path"]
    models_target = paths["models_dir"]
    force_download = bool(opts.get("setup_force_download", False))
    verify_checksum = bool(opts.get("verify_checksum", True))
    timeout_sec = int(opts.get("setup_download_timeout_sec", 1200) or 1200)

    with _AUTO_SETUP_LOCK:
        if _is_binary_ready(binary_target) and _looks_like_model_dir(models_target) and not force_download:
            return {
                "ok": True,
                "error": "",
                "binary_path": binary_target,
                "model_dir": models_target,
                "version": _REALESRGAN_RELEASE.get("version", ""),
            }

        os.makedirs(paths["downloads_dir"], exist_ok=True)
        archive_path = os.path.join(paths["downloads_dir"], archive_name)

        need_download = force_download or not os.path.isfile(archive_path)
        if not need_download and verify_checksum and expected_sha:
            try:
                current_sha = _sha256_file(archive_path)
                if current_sha != expected_sha:
                    need_download = True
            except Exception:
                need_download = True

        if need_download:
            _log(f"Downloading RealESRGAN package: {archive_name}")
            try:
                _download_file(archive_url, archive_path, timeout_sec=timeout_sec)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"download failed: {exc}",
                    "binary_path": "",
                    "model_dir": "",
                }

        if verify_checksum and expected_sha:
            try:
                checksum = _sha256_file(archive_path)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"checksum read failed: {exc}",
                    "binary_path": "",
                    "model_dir": "",
                }
            if checksum.lower() != expected_sha.lower():
                try:
                    os.remove(archive_path)
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error": "checksum mismatch for downloaded RealESRGAN package",
                    "binary_path": "",
                    "model_dir": "",
                }

        with tempfile.TemporaryDirectory(prefix="autocapcut_realesrgan_unpack_") as tmp_dir:
            try:
                _safe_extract_zip(archive_path, tmp_dir)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"extract failed: {exc}",
                    "binary_path": "",
                    "model_dir": "",
                }

            discovered_binary = _discover_binary(tmp_dir)
            if not discovered_binary:
                return {
                    "ok": False,
                    "error": "realesrgan binary not found in package",
                    "binary_path": "",
                    "model_dir": "",
                }

            discovered_models = _discover_models_dir(tmp_dir)
            if not discovered_models:
                return {
                    "ok": False,
                    "error": "model directory not found in package",
                    "binary_path": "",
                    "model_dir": "",
                }

            os.makedirs(paths["bin_dir"], exist_ok=True)
            shutil.copy2(discovered_binary, binary_target)
            if os.name != "nt":
                try:
                    os.chmod(binary_target, 0o755)
                except Exception:
                    pass

            try:
                _sync_model_dir(discovered_models, models_target)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"copy models failed: {exc}",
                    "binary_path": "",
                    "model_dir": "",
                }

        try:
            state = {
                "version": _REALESRGAN_RELEASE.get("version", ""),
                "platform": platform_key,
                "asset_name": archive_name,
                "archive_sha256": expected_sha,
                "binary_path": binary_target,
                "model_dir": models_target,
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            os.makedirs(setup_root, exist_ok=True)
            with open(paths["state_file"], "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        _log("RealESRGAN package is ready.")
        return {
            "ok": True,
            "error": "",
            "binary_path": binary_target,
            "model_dir": models_target,
            "version": _REALESRGAN_RELEASE.get("version", ""),
        }


def _run_realesrgan(input_path, output_path, scale, options, log_callback=None):
    auto_setup_error = ""
    options = dict(options)  # don't mutate caller's dict
    setup_root = str(options.get("setup_dir") or _default_setup_root()).strip()

    # Fast path: use pre-warmed cache → zero setup overhead per image
    with _SETUP_CACHE_LOCK:
        cached_binary = _SETUP_CACHE.get("binary_path", "")
        cached_model  = _SETUP_CACHE.get("model_dir", "")

    if cached_binary and _is_binary_ready(cached_binary):
        if not options.get("bin_path"):
            options["bin_path"] = cached_binary
        if not options.get("model_dir") and cached_model:
            options["model_dir"] = cached_model
    elif bool(options.get("auto_setup", True)):
        # Slow path: first time or cache miss → run setup and populate cache
        setup_result = ensure_realesrgan_assets(options=options, log_callback=log_callback)
        if setup_result.get("ok"):
            with _SETUP_CACHE_LOCK:
                _SETUP_CACHE["binary_path"] = setup_result.get("binary_path", "")
                _SETUP_CACHE["model_dir"]   = setup_result.get("model_dir", "")
            if not options.get("bin_path"):
                options["bin_path"] = setup_result.get("binary_path", "")
            if not options.get("model_dir"):
                options["model_dir"] = setup_result.get("model_dir", "")
        else:
            auto_setup_error = str(setup_result.get("error") or "").strip()

    binary = _find_binary(options.get("bin_path"))
    if not binary:
        if auto_setup_error:
            return False, auto_setup_error
        return False, "realesrgan binary not found"

    requested_scale = max(1, int(scale))
    cmd = [binary, "-i", input_path, "-o", output_path, "-s", str(min(requested_scale, 4))]

    model_dir = _resolve_model_dir(options.get("model_dir"), setup_root)
    if model_dir and not os.path.isdir(model_dir):
        model_dir = ""
    model_name = str(options.get("model_name") or "").strip()
    gpu_id = str(options.get("gpu_id") or "").strip()
    tile_size = int(options.get("tile_size") or 0)
    tta_mode = bool(options.get("tta_mode"))
    timeout_sec = int(options.get("timeout_sec") or 300)

    if model_dir:
        cmd.extend(["-m", model_dir])
    if model_name:
        cmd.extend(["-n", model_name])
    if gpu_id:
        cmd.extend(["-g", gpu_id])
    if tile_size > 0:
        cmd.extend(["-t", str(tile_size)])
    if tta_mode:
        cmd.append("-x")

    ext = os.path.splitext(output_path)[1].lower().replace(".", "")
    if ext in {"jpg", "jpeg", "png", "webp"}:
        cmd.extend(["-f", "jpg" if ext == "jpeg" else ext])

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "realesrgan timeout"
    except Exception as exc:
        return False, str(exc)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail:
            detail = detail[-700:]
        return False, detail or f"realesrgan exited with code {proc.returncode}"

    if not os.path.isfile(output_path):
        return False, "realesrgan completed without output file"
    return True, ""


def _run_opencv(input_path, output_path, scale, options):
    try:
        import cv2
        import numpy as np
    except Exception as exc:
        return False, f"opencv unavailable: {exc}"

    try:
        raw = np.fromfile(input_path, dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
        if image is None:
            return False, "opencv failed to read image"
        height, width = image.shape[:2]
        target_w = int(options.get("target_width") or 0)
        target_h = int(options.get("target_height") or 0)
        if target_w <= 0 or target_h <= 0:
            target_w = max(1, int(round(width * float(scale))))
            target_h = max(1, int(round(height * float(scale))))
        ok, err = _write_resized_image(
            image=image,
            output_path=output_path,
            target_width=target_w,
            target_height=target_h,
            options=options,
            cv2_mod=cv2,
        )
        if not ok:
            return False, err
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _read_image_dimensions(path):
    try:
        from PIL import Image
        with Image.open(path) as img:
            # Image.open() is lazy — only reads the header, not all pixel data.
            # Calling img.size triggers header parsing, which is very fast.
            width, height = img.size
            return int(width), int(height)
    except Exception:
        pass

    # Fallback: full cv2 decode (slower, kept for exotic formats PIL can't read)
    try:
        import cv2
        import numpy as np
        raw = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
        if image is None:
            return 0, 0
        height, width = image.shape[:2]
        return int(width), int(height)
    except Exception:
        return 0, 0


def _write_resized_image(image, output_path, target_width, target_height, options, cv2_mod=None):
    cv2 = cv2_mod
    if cv2 is None:
        try:
            import cv2 as cv2
        except Exception as exc:
            return False, f"opencv unavailable: {exc}"

    try:
        resized = cv2.resize(
            image,
            (max(1, int(target_width)), max(1, int(target_height))),
            interpolation=cv2.INTER_LANCZOS4,
        )
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        ext = os.path.splitext(output_path)[1].lower()
        encode_ext = ext if ext in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
        params = []
        if encode_ext in {".jpg", ".jpeg"}:
            params = [int(cv2.IMWRITE_JPEG_QUALITY), int(options.get("jpg_quality") or 95)]
        elif encode_ext == ".png":
            params = [int(cv2.IMWRITE_PNG_COMPRESSION), int(options.get("png_compression") or 3)]

        ok, encoded = cv2.imencode(encode_ext, resized, params)
        if not ok:
            return False, "opencv failed to encode output"
        encoded.tofile(output_path)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _resize_file_to_dimensions(input_path, output_path, target_width, target_height, options):
    try:
        import cv2
        import numpy as np
    except Exception as exc:
        return False, f"opencv unavailable: {exc}"

    try:
        raw = np.fromfile(input_path, dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
        if image is None:
            return False, "opencv failed to read image"
        return _write_resized_image(
            image=image,
            output_path=output_path,
            target_width=target_width,
            target_height=target_height,
            options=options,
            cv2_mod=cv2,
        )
    except Exception as exc:
        return False, str(exc)


def _resolve_target_plan(input_path, scale, options):
    src_w, src_h = _read_image_dimensions(input_path)
    target_preset = _clean_target_preset(options.get("target_preset"))

    try:
        target_w = int(options.get("target_width") or 0)
    except Exception:
        target_w = 0
    try:
        target_h = int(options.get("target_height") or 0)
    except Exception:
        target_h = 0

    if target_preset and (target_w <= 0 or target_h <= 0):
        target_w, target_h = _preset_dimensions(target_preset)

    plan = {
        "source_width": src_w,
        "source_height": src_h,
        "target_preset": target_preset,
        "target_width": target_w,
        "target_height": target_h,
        "effective_scale": max(1.0, float(scale or 1)),
        "engine_scale": max(1, int(scale or 1)),
        "copy_only": False,
    }

    if src_w <= 0 or src_h <= 0:
        return plan

    if target_w > 0 and target_h > 0:
        if (src_h > src_w) != (target_h > target_w):
            target_w, target_h = target_h, target_w

        fit_ratio = min(float(target_w) / float(src_w), float(target_h) / float(src_h))
        if fit_ratio <= 1.0:
            plan["target_width"] = src_w
            plan["target_height"] = src_h
            plan["effective_scale"] = 1.0
            plan["engine_scale"] = 1
            plan["copy_only"] = True
            return plan

        final_w = max(1, int(round(src_w * fit_ratio)))
        final_h = max(1, int(round(src_h * fit_ratio)))
        plan["target_width"] = final_w
        plan["target_height"] = final_h
        plan["effective_scale"] = fit_ratio
        plan["engine_scale"] = max(1, int(math.ceil(fit_ratio)))
        return plan

    fallback_scale = max(1, int(scale or 1))
    plan["target_width"] = max(1, int(round(src_w * float(fallback_scale))))
    plan["target_height"] = max(1, int(round(src_h * float(fallback_scale))))
    plan["effective_scale"] = float(fallback_scale)
    plan["engine_scale"] = fallback_scale
    return plan


def upscale_image_file(input_path, output_path, scale=2, engine="auto", options=None, log_callback=None):
    started = time.time()
    opts = dict(options or {})
    selected_engine = _clean_engine(engine)
    error_messages = []
    plan = _resolve_target_plan(input_path, scale, opts)
    if plan.get("target_width"):
        opts["target_width"] = int(plan["target_width"])
    if plan.get("target_height"):
        opts["target_height"] = int(plan["target_height"])
    if plan.get("target_preset"):
        opts["target_preset"] = plan["target_preset"]

    def _log(msg):
        if log_callback:
            log_callback(str(msg))

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    if plan.get("copy_only"):
        try:
            shutil.copyfile(input_path, output_path)
            return {
                "ok": True,
                "output_path": output_path,
                "engine": "noop",
                "error": "",
                "elapsed_ms": int((time.time() - started) * 1000),
                "width": int(plan.get("target_width") or 0),
                "height": int(plan.get("target_height") or 0),
                "target_preset": str(plan.get("target_preset") or ""),
                "effective_scale": float(plan.get("effective_scale") or 1.0),
            }
        except Exception as exc:
            error_messages.append(f"copy: {exc}")

    if selected_engine in {"auto", "realesrgan"}:
        temp_output_path = output_path
        needs_exact_resize = (
            int(plan.get("target_width") or 0) > 0
            and int(plan.get("target_height") or 0) > 0
            and int(plan.get("source_width") or 0) > 0
            and int(plan.get("source_height") or 0) > 0
            and (
                int(plan.get("source_width") or 0) * int(plan.get("engine_scale") or 1) != int(plan.get("target_width") or 0)
                or int(plan.get("source_height") or 0) * int(plan.get("engine_scale") or 1) != int(plan.get("target_height") or 0)
            )
        )
        temp_cleanup_path = ""
        if needs_exact_resize:
            fd, temp_output_path = tempfile.mkstemp(
                prefix="autocapcut_upscale_rg_",
                suffix=os.path.splitext(output_path)[1] or ".jpg",
                dir=out_dir or None,
            )
            os.close(fd)
            temp_cleanup_path = temp_output_path

        ok, err = _run_realesrgan(
            input_path,
            temp_output_path,
            int(plan.get("engine_scale") or scale or 1),
            opts,
            log_callback=_log,
        )
        if ok:
            if needs_exact_resize:
                ok, err = _resize_file_to_dimensions(
                    temp_output_path,
                    output_path,
                    int(plan.get("target_width") or 0),
                    int(plan.get("target_height") or 0),
                    opts,
                )
                try:
                    os.remove(temp_output_path)
                except Exception:
                    pass
                if not ok:
                    error_messages.append(f"realesrgan-resize: {err}")
                else:
                    temp_cleanup_path = ""
            if not err:
                return {
                    "ok": True,
                    "output_path": output_path,
                    "engine": "realesrgan",
                    "error": "",
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "width": int(plan.get("target_width") or 0),
                    "height": int(plan.get("target_height") or 0),
                    "target_preset": str(plan.get("target_preset") or ""),
                    "effective_scale": float(plan.get("effective_scale") or scale or 1.0),
                }
            if temp_cleanup_path:
                try:
                    os.remove(temp_cleanup_path)
                except Exception:
                    pass
        if temp_cleanup_path:
            try:
                os.remove(temp_cleanup_path)
            except Exception:
                pass
        error_messages.append(f"realesrgan: {err}")
        if selected_engine == "realesrgan":
            return {
                "ok": False,
                "output_path": output_path,
                "engine": "realesrgan",
                "error": "; ".join(error_messages),
                "elapsed_ms": int((time.time() - started) * 1000),
                "width": int(plan.get("target_width") or 0),
                "height": int(plan.get("target_height") or 0),
                "target_preset": str(plan.get("target_preset") or ""),
                "effective_scale": float(plan.get("effective_scale") or scale or 1.0),
            }
        _log(f"Realesrgan upscale failed, fallback to OpenCV: {err}")

    if selected_engine in {"auto", "opencv"}:
        ok, err = _run_opencv(input_path, output_path, float(plan.get("effective_scale") or scale or 1.0), opts)
        if ok:
            return {
                "ok": True,
                "output_path": output_path,
                "engine": "opencv",
                "error": "",
                "elapsed_ms": int((time.time() - started) * 1000),
                "width": int(plan.get("target_width") or 0),
                "height": int(plan.get("target_height") or 0),
                "target_preset": str(plan.get("target_preset") or ""),
                "effective_scale": float(plan.get("effective_scale") or scale or 1.0),
            }
        error_messages.append(f"opencv: {err}")

    return {
        "ok": False,
        "output_path": output_path,
        "engine": selected_engine,
        "error": "; ".join(error_messages) or "upscale failed",
        "elapsed_ms": int((time.time() - started) * 1000),
        "width": int(plan.get("target_width") or 0),
        "height": int(plan.get("target_height") or 0),
        "target_preset": str(plan.get("target_preset") or ""),
        "effective_scale": float(plan.get("effective_scale") or scale or 1.0),
    }


def pre_warm_realesrgan(options=None, log_callback=None):
    """
    Call once before batch generation starts to:
      1. Run ensure_realesrgan_assets (download/verify binary & models if needed)
      2. Discover and cache the binary path
    Subsequent per-image upscale calls skip setup entirely → ~50-200ms saved per image.
    """
    opts = dict(options or {})
    if not opts.get("auto_setup", True):
        return

    def _log(msg):
        if log_callback:
            log_callback(str(msg))

    # Return immediately if cache is already warm and valid
    with _SETUP_CACHE_LOCK:
        cached = _SETUP_CACHE.get("binary_path", "")
    if cached and _is_binary_ready(cached):
        _log(f"RealESRGAN already warmed: {cached}")
        return

    _log("Pre-warming RealESRGAN (one-time setup check)...")
    result = ensure_realesrgan_assets(options=opts, log_callback=_log)
    if result.get("ok"):
        binary = result.get("binary_path", "")
        model  = result.get("model_dir", "")
        with _SETUP_CACHE_LOCK:
            _SETUP_CACHE["binary_path"] = binary
            _SETUP_CACHE["model_dir"]   = model
        # Also prime the binary-path cache
        with _BINARY_CACHE_LOCK:
            global _BINARY_CACHE
            _BINARY_CACHE = binary
        _log(f"RealESRGAN pre-warm done — binary: {binary}")
    else:
        _log(f"RealESRGAN pre-warm failed: {result.get('error', 'unknown error')}")
