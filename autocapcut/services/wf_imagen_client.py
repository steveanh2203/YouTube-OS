import json
import mimetypes
import os
import time
from urllib.parse import urlparse

from curl_cffi import requests as http_requests


BASE_URL = str(os.getenv("AUTO_WHISK_IMAGEN_BASE_URL", "https://api.ai33.pro")).strip().rstrip("/")
DEFAULT_TIMEOUT = float(os.getenv("AUTO_WHISK_IMAGEN_TIMEOUT", "45"))
DEFAULT_POLL_INTERVAL = float(os.getenv("AUTO_WHISK_IMAGEN_POLL_INTERVAL", "3"))
DEFAULT_TASK_TIMEOUT = float(os.getenv("AUTO_WHISK_IMAGEN_TASK_TIMEOUT", "900"))


def _extract_error_message(payload):
    if isinstance(payload, dict):
        for key in ("message", "error_message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _safe_json(response):
    try:
        return response.json()
    except Exception:
        return {}


def _headers(api_key, include_json=False):
    headers = {"xi-api-key": str(api_key or "").strip()}
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


def _sleep_with_stop(seconds, stop_event=None):
    wait_seconds = max(0.0, float(seconds or 0.0))
    if wait_seconds <= 0:
        return True
    if not stop_event:
        time.sleep(wait_seconds)
        return True
    end_at = time.time() + wait_seconds
    while time.time() < end_at:
        if stop_event.is_set():
            return False
        remaining = end_at - time.time()
        time.sleep(min(0.2, max(0.0, remaining)))
    return not stop_event.is_set()


def list_models(api_key, timeout=DEFAULT_TIMEOUT):
    url = f"{BASE_URL}/v1i/models"
    try:
        response = http_requests.get(
            url,
            headers=_headers(api_key),
            timeout=timeout,
            impersonate="chrome131",
        )
    except Exception as exc:
        return False, [], f"Unable to load models: {exc}"

    payload = _safe_json(response)
    if response.status_code != 200:
        return False, [], _extract_error_message(payload) or f"Model list request failed ({response.status_code})."
    if not bool(payload.get("success", False)):
        return False, [], _extract_error_message(payload) or "Model list request failed."

    models = payload.get("models")
    if not isinstance(models, list):
        return False, [], "Model list response is invalid."
    return True, models, ""


def get_price(api_key, model_id, generations_count=1, model_parameters=None, assets=0, timeout=DEFAULT_TIMEOUT):
    url = f"{BASE_URL}/v1i/task/price"
    payload = {
        "model_id": str(model_id or "").strip(),
        "generations_count": int(generations_count or 1),
        "model_parameters": dict(model_parameters or {}),
        "assets": int(assets or 0),
    }

    try:
        response = http_requests.post(
            url,
            headers=_headers(api_key, include_json=True),
            json=payload,
            timeout=timeout,
            impersonate="chrome131",
        )
    except Exception as exc:
        return False, 0, f"Unable to calculate price: {exc}"

    body = _safe_json(response)
    if response.status_code != 200:
        return False, 0, _extract_error_message(body) or f"Price request failed ({response.status_code})."
    if not bool(body.get("success", False)):
        return False, 0, _extract_error_message(body) or "Price request failed."

    try:
        credits = int(body.get("credits", 0))
    except Exception:
        credits = 0
    return True, credits, ""


def create_generation_task(
    api_key,
    prompt,
    model_id,
    generations_count=1,
    model_parameters=None,
    assets=None,
    receive_url="",
    timeout=DEFAULT_TIMEOUT,
):
    url = f"{BASE_URL}/v1i/task/generate-image"
    form_data = {
        "prompt": str(prompt or ""),
        "model_id": str(model_id or "").strip(),
        "generations_count": str(int(generations_count or 1)),
    }
    if model_parameters:
        form_data["model_parameters"] = json.dumps(model_parameters, ensure_ascii=False)
    if str(receive_url or "").strip():
        form_data["receive_url"] = str(receive_url).strip()

    open_files = []
    file_parts = []
    for asset_path in assets or []:
        abs_path = os.path.abspath(str(asset_path or "").strip())
        if not abs_path:
            continue
        try:
            file_handle = open(abs_path, "rb")
        except Exception as exc:
            return False, {}, f"Cannot open asset file: {abs_path} ({exc})"
        open_files.append(file_handle)
        mime_type = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
        file_parts.append(("assets", (os.path.basename(abs_path), file_handle, mime_type)))

    try:
        response = http_requests.post(
            url,
            headers=_headers(api_key),
            data=form_data,
            files=file_parts or None,
            timeout=timeout,
            impersonate="chrome131",
        )
    except Exception as exc:
        return False, {}, f"Unable to create generation task: {exc}"
    finally:
        for handle in open_files:
            try:
                handle.close()
            except Exception:
                pass

    payload = _safe_json(response)
    if response.status_code not in (200, 201):
        return False, payload, _extract_error_message(payload) or f"Generate request failed ({response.status_code})."
    if not bool(payload.get("success", False)):
        return False, payload, _extract_error_message(payload) or "Generate request failed."

    task_id = str(payload.get("task_id", "")).strip()
    if not task_id:
        return False, payload, "Task created but no task_id was returned."
    return True, payload, ""


def get_task_status(api_key, task_id, timeout=DEFAULT_TIMEOUT):
    task = str(task_id or "").strip()
    if not task:
        return False, {}, "Task ID is missing."
    url = f"{BASE_URL}/v1/task/{task}"
    try:
        response = http_requests.get(
            url,
            headers=_headers(api_key, include_json=True),
            timeout=timeout,
            impersonate="chrome131",
        )
    except Exception as exc:
        return False, {}, f"Unable to fetch task status: {exc}"

    payload = _safe_json(response)
    if response.status_code != 200:
        return False, payload, _extract_error_message(payload) or f"Task status request failed ({response.status_code})."
    return True, payload, ""


def poll_task_until_done(
    api_key,
    task_id,
    stop_event=None,
    poll_interval=DEFAULT_POLL_INTERVAL,
    timeout_sec=DEFAULT_TASK_TIMEOUT,
):
    start_at = time.time()
    while True:
        if stop_event and stop_event.is_set():
            return False, {}, "STOPPED"

        ok, payload, err = get_task_status(api_key, task_id)
        if not ok:
            return False, payload, err

        status = str(payload.get("status", "")).strip().lower()
        if status == "done":
            return True, payload, ""
        if status == "error":
            reason = str(payload.get("error_message", "")).strip()
            if not reason:
                reason = _extract_error_message(payload) or "Image generation failed."
            return False, payload, reason

        if time.time() - start_at > max(30.0, float(timeout_sec or DEFAULT_TASK_TIMEOUT)):
            return False, payload, "Task polling timed out."

        if not _sleep_with_stop(poll_interval, stop_event):
            return False, payload, "STOPPED"


def _extension_from_mime(mime_type):
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }
    return mapping.get(str(mime_type or "").strip().lower(), "")


def guess_file_extension(image_item):
    if isinstance(image_item, dict):
        ext = _extension_from_mime(image_item.get("mimeType"))
        if ext:
            return ext

        for key in ("imageUrl", "previewUrl"):
            url = str(image_item.get(key, "")).strip()
            if not url:
                continue
            path = urlparse(url).path
            candidate = os.path.splitext(path)[1].lower()
            if candidate in {".png", ".jpg", ".jpeg", ".webp"}:
                if candidate == ".jpeg":
                    return ".jpg"
                return candidate
    return ".png"


def download_image(url, output_path, timeout=DEFAULT_TIMEOUT):
    target = str(output_path or "").strip()
    source = str(url or "").strip()
    if not source:
        return False, "Image URL is empty."
    if not target:
        return False, "Output path is empty."

    try:
        response = http_requests.get(source, timeout=timeout, impersonate="chrome131")
    except Exception as exc:
        return False, f"Unable to download image: {exc}"
    if response.status_code != 200:
        return False, f"Image download failed ({response.status_code})."

    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        with open(target, "wb") as f:
            f.write(response.content)
    except Exception as exc:
        return False, f"Unable to save image: {exc}"
    return True, ""


def save_result_images(result_images, output_folder, index):
    if not isinstance(result_images, list) or not result_images:
        return False, [], "No images returned by IMAGEN."

    base = f"image_{int(index):03d}"
    saved = []
    first_error = ""

    for i, item in enumerate(result_images, 1):
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("imageUrl") or item.get("previewUrl") or "").strip()
        if not image_url:
            continue
        ext = guess_file_extension(item)
        suffix = "" if i == 1 else f"_v{i}"
        target = os.path.join(output_folder, f"{base}{suffix}{ext}")
        ok, err = download_image(image_url, target)
        if ok:
            saved.append(target)
        elif not first_error:
            first_error = err

    if not saved:
        return False, [], first_error or "No downloadable images were returned."
    return True, saved, ""
