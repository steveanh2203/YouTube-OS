"""Roxy Browser API + YouTube Studio upload helpers."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from loguru import logger


class RoxyUploadError(RuntimeError):
    """Raised when Roxy upload operations fail."""


ProgressCallback = Callable[[str], None]
DEFAULT_RATE_LIMIT_PER_MINUTE = 50
_RATE_WINDOW_SECONDS = 60.0
_RATE_LOCK = threading.Lock()
_REQUEST_TIMESTAMPS: deque[float] = deque()
_SERVER_LIMIT_PER_MINUTE: int | None = None
_SERVER_REMAINING: int | None = None
_SERVER_RESET_AT: float | None = None
_SERVER_LAST_SEEN_AT: float | None = None


@dataclass(slots=True)
class RoxyProfile:
    """Lightweight Roxy profile metadata."""

    dir_id: str
    window_name: str
    window_sort_num: int | None = None

    @property
    def display_name(self) -> str:
        label = self.window_name.strip() if self.window_name else "Untitled profile"
        if self.window_sort_num is not None:
            return f"#{self.window_sort_num} · {label}"
        return label


@dataclass(slots=True)
class RoxyWorkspace:
    """Workspace metadata from Roxy."""

    workspace_id: int
    workspace_name: str


@dataclass(slots=True)
class RoxyUploadSummary:
    """Result after attempting a YouTube upload start."""

    profile_id: str
    video_path: Path
    debugger_address: str
    message: str


@dataclass(slots=True)
class RoxyPreflightResult:
    """Resolved inputs validated before upload starts."""

    workspace_id: int
    profile_id: str
    profile_display_name: str
    video_path: Path


@dataclass(slots=True)
class RoxyRateLimitSnapshot:
    """Snapshot of request usage in the latest rolling minute."""

    limit_per_minute: int
    used_last_minute: int
    remaining: int
    source: str  # "local-estimate" or "server-header"
    reset_in_seconds: int | None = None


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _cleanup_request_window_locked(now: float) -> None:
    while _REQUEST_TIMESTAMPS and (now - _REQUEST_TIMESTAMPS[0]) > _RATE_WINDOW_SECONDS:
        _REQUEST_TIMESTAMPS.popleft()


def _header_value(headers, *names: str) -> str | None:
    if headers is None:
        return None
    for name in names:
        try:
            value = headers.get(name)
        except Exception:
            value = None
        if value is not None:
            return str(value)
    return None


def _record_rate_limit(headers=None) -> None:
    global _SERVER_LAST_SEEN_AT, _SERVER_LIMIT_PER_MINUTE, _SERVER_REMAINING, _SERVER_RESET_AT
    now = time.time()
    with _RATE_LOCK:
        _REQUEST_TIMESTAMPS.append(now)
        _cleanup_request_window_locked(now)

        limit_value = _safe_int(
            _header_value(
                headers,
                "X-RateLimit-Limit",
                "x-ratelimit-limit",
                "RateLimit-Limit",
            )
        )
        remaining_value = _safe_int(
            _header_value(
                headers,
                "X-RateLimit-Remaining",
                "x-ratelimit-remaining",
                "RateLimit-Remaining",
            )
        )
        reset_value = _safe_int(
            _header_value(
                headers,
                "X-RateLimit-Reset",
                "x-ratelimit-reset",
                "RateLimit-Reset",
            )
        )

        if limit_value is not None and limit_value > 0:
            _SERVER_LIMIT_PER_MINUTE = limit_value
            _SERVER_LAST_SEEN_AT = now
        if remaining_value is not None and remaining_value >= 0:
            _SERVER_REMAINING = remaining_value
            _SERVER_LAST_SEEN_AT = now
        if reset_value is not None:
            # Some APIs return seconds-until-reset, others return unix epoch.
            if reset_value > int(now) + 600:
                _SERVER_RESET_AT = float(reset_value)
            else:
                _SERVER_RESET_AT = now + float(max(reset_value, 0))
            _SERVER_LAST_SEEN_AT = now


def get_roxy_rate_limit_snapshot(
    *,
    default_limit: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
) -> RoxyRateLimitSnapshot:
    """Return current API usage estimate (or server header data when available)."""
    now = time.time()
    with _RATE_LOCK:
        _cleanup_request_window_locked(now)
        used = len(_REQUEST_TIMESTAMPS)
        limit = _SERVER_LIMIT_PER_MINUTE if _SERVER_LIMIT_PER_MINUTE is not None else max(default_limit, 1)
        local_remaining = max(limit - used, 0)

        source = "local-estimate"
        remaining = local_remaining
        reset_in_seconds: int | None = None

        if _SERVER_LAST_SEEN_AT is not None and (now - _SERVER_LAST_SEEN_AT) <= 90:
            if _SERVER_REMAINING is not None:
                remaining = max(min(_SERVER_REMAINING, limit), 0)
                source = "server-header"
            if _SERVER_RESET_AT is not None:
                reset_in_seconds = max(int(round(_SERVER_RESET_AT - now)), 0)

        return RoxyRateLimitSnapshot(
            limit_per_minute=limit,
            used_last_minute=used,
            remaining=remaining,
            source=source,
            reset_in_seconds=reset_in_seconds,
        )


def run_roxy_upload_preflight(
    *,
    api_host: str,
    api_token: str,
    workspace_id: int,
    profile_id: str,
    video_path: Path,
) -> RoxyPreflightResult:
    """Validate upload prerequisites and resolve a usable workspace/profile pair."""
    if not api_host.strip():
        raise RoxyUploadError("Roxy API host is required.")
    if not api_token.strip():
        raise RoxyUploadError("Roxy API key/token is required.")
    if workspace_id <= 0:
        raise RoxyUploadError("Workspace ID must be greater than 0.")
    if not profile_id.strip():
        raise RoxyUploadError("Roxy profile ID is required.")
    if not video_path.exists() or not video_path.is_file():
        raise RoxyUploadError(f"Video file does not exist: {video_path}")

    client = RoxyApiClient(api_host, api_token)

    workspaces = client.list_workspaces()
    if not workspaces:
        raise RoxyUploadError("No accessible Roxy workspace found for this API key.")

    available_workspace_ids = {item.workspace_id for item in workspaces}
    resolved_workspace_id = workspace_id
    if resolved_workspace_id not in available_workspace_ids:
        resolved_workspace_id = workspaces[0].workspace_id

    profiles = client.list_profiles(resolved_workspace_id, page_size=200)
    if not profiles:
        raise RoxyUploadError(
            f"Workspace {resolved_workspace_id} has no profiles. Create/import profiles in Roxy first."
        )

    target_profile = next((item for item in profiles if item.dir_id == profile_id.strip()), None)
    if target_profile is None:
        options = ", ".join(item.display_name for item in profiles[:6])
        raise RoxyUploadError(
            "Selected profile is not in the resolved workspace.\n"
            f"Workspace: {resolved_workspace_id}\n"
            f"Profile ID: {profile_id.strip()}\n"
            f"Available samples: {options}"
        )

    return RoxyPreflightResult(
        workspace_id=resolved_workspace_id,
        profile_id=target_profile.dir_id,
        profile_display_name=target_profile.display_name,
        video_path=video_path.resolve(),
    )


class RoxyApiClient:
    """Tiny HTTP client for the local Roxy API."""

    def __init__(self, api_host: str, token: str, *, timeout_sec: float = 18.0) -> None:
        host = api_host.strip()
        if not host:
            raise RoxyUploadError("Roxy API host is required.")
        if "://" not in host:
            host = f"http://{host}"
        self.base_url = host.rstrip("/")
        self.token = token.strip()
        self.timeout_sec = timeout_sec

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        query: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        if query:
            encoded = urlencode({k: v for k, v in query.items() if v is not None})
            if encoded:
                url = f"{url}?{encoded}"

        body: bytes | None = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["token"] = self.token

        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")

        request = Request(url=url, data=body, headers=headers, method=method.upper())

        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                _record_rate_limit(response.headers)
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            _record_rate_limit(exc.headers)
            detail = exc.read().decode("utf-8", errors="replace").strip()
            message = detail or str(exc)
            raise RoxyUploadError(f"Roxy API request failed ({exc.code}): {message}") from exc
        except URLError as exc:
            _record_rate_limit(None)
            raise RoxyUploadError(f"Could not connect to Roxy API at {self.base_url}: {exc}") from exc

        try:
            result = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise RoxyUploadError("Roxy API returned invalid JSON.") from exc

        if isinstance(result, dict):
            code = result.get("code")
            if code not in (None, 0):
                msg = str(result.get("msg") or "Unknown API error")
                raise RoxyUploadError(f"Roxy API error ({code}): {msg}")
            return result

        raise RoxyUploadError("Unexpected response format from Roxy API.")

    def list_profiles(self, workspace_id: int, *, page_index: int = 1, page_size: int = 100) -> list[RoxyProfile]:
        response = self._request(
            "GET",
            "/browser/list_v3",
            query={
                "workspaceId": workspace_id,
                "page_index": page_index,
                "page_size": page_size,
            },
        )
        data = response.get("data", {})
        rows = data.get("rows", []) if isinstance(data, dict) else []
        profiles: list[RoxyProfile] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            dir_id = str(row.get("dirId") or "").strip()
            if not dir_id:
                continue
            sort_num = row.get("windowSortNum")
            profiles.append(
                RoxyProfile(
                    dir_id=dir_id,
                    window_name=str(row.get("windowName") or "").strip(),
                    window_sort_num=int(sort_num) if isinstance(sort_num, int) else None,
                )
            )
        return profiles

    def list_workspaces(self, *, page_index: int = 1, page_size: int = 50) -> list[RoxyWorkspace]:
        response = self._request(
            "GET",
            "/browser/workspace",
            query={
                "page_index": page_index,
                "page_size": page_size,
            },
        )
        data = response.get("data", {})
        rows = data.get("rows", []) if isinstance(data, dict) else []
        workspaces: list[RoxyWorkspace] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_id = row.get("id")
            if not isinstance(raw_id, int):
                continue
            workspaces.append(
                RoxyWorkspace(
                    workspace_id=raw_id,
                    workspace_name=str(row.get("workspaceName") or "").strip(),
                )
            )
        return workspaces

    def open_profile(
        self,
        *,
        workspace_id: int,
        profile_id: str,
        args: list[str] | None = None,
        force_open: bool = True,
    ) -> dict:
        response = self._request(
            "POST",
            "/browser/open",
            payload={
                "workspaceId": workspace_id,
                "dirId": profile_id,
                "args": args or [],
                "forceOpen": bool(force_open),
            },
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise RoxyUploadError("Roxy API did not return open profile connection data.")
        return data

    def close_profile(self, profile_id: str) -> None:
        self._request("POST", "/browser/close", payload={"dirId": profile_id})


def _normalize_debugger_address(raw: str) -> str:
    address = raw.strip()
    if address.startswith("http://"):
        return address[len("http://"):]
    if address.startswith("https://"):
        return address[len("https://"):]
    return address


def _require_selenium():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
    except ModuleNotFoundError as exc:
        install_cmd = f"{sys.executable} -m pip install selenium"
        raise RoxyUploadError(
            "Missing dependency: selenium.\n"
            f"Install into the app interpreter with:\n{install_cmd}"
        ) from exc
    return webdriver, Service, By


def _find_upload_input_in_shadow_dom(driver):
    script = """
const queue = [document];
while (queue.length) {
  const root = queue.shift();
  if (!root || !root.querySelectorAll) {
    continue;
  }

  const inputs = root.querySelectorAll("input[type='file']");
  for (const el of inputs) {
    const accept = (el.getAttribute("accept") || "").toLowerCase();
    if (accept.includes("video")) {
      return el;
    }
  }
  if (inputs.length > 0) {
    return inputs[0];
  }

  const nodes = root.querySelectorAll("*");
  for (const node of nodes) {
    if (node.shadowRoot) {
      queue.push(node.shadowRoot);
    }
  }
}
return null;
"""
    try:
        return driver.execute_script(script)
    except Exception:
        return None


def _find_upload_input(driver, by, *, timeout_sec: float) -> object | None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        candidates = driver.find_elements(by.CSS_SELECTOR, "input[type='file']")
        if candidates:
            for element in candidates:
                accept = (element.get_attribute("accept") or "").lower()
                if "video" in accept:
                    return element
            return candidates[0]

        from_shadow = _find_upload_input_in_shadow_dom(driver)
        if from_shadow is not None:
            return from_shadow

        time.sleep(0.35)
    return None


def _click_first_visible(driver, by, selectors: list[str]) -> bool:
    for selector in selectors:
        elements = driver.find_elements(by.CSS_SELECTOR, selector)
        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception:
                continue
    return False


def _click_upload_menu_item(driver, by) -> bool:
    xpath_candidates = [
        "//*[contains(translate(normalize-space(.), 'UPLOADVIDEOS', 'uploadvideos'), 'upload') and contains(translate(normalize-space(.), 'VIDEOS', 'videos'), 'video')]",
        "//*[contains(normalize-space(.), 'Upload video')]",
        "//*[contains(normalize-space(.), 'Upload videos')]",
        "//*[contains(normalize-space(.), 'Tải video')]",
        "//*[contains(normalize-space(.), 'Đăng video')]",
    ]
    for xpath in xpath_candidates:
        nodes = driver.find_elements(by.XPATH, xpath)
        for node in nodes:
            try:
                if not node.is_displayed():
                    continue
                driver.execute_script("arguments[0].click();", node)
                return True
            except Exception:
                continue
    return False


def _extract_channel_id(url: str) -> str | None:
    match = re.search(r"/channel/(UC[\w-]+)", url)
    if not match:
        return None
    return match.group(1)


def _discover_channel_id(driver) -> str | None:
    current_url = ""
    try:
        current_url = driver.current_url
    except Exception:
        current_url = ""

    direct = _extract_channel_id(current_url)
    if direct:
        return direct

    script = """
const anchors = Array.from(document.querySelectorAll("a[href*='/channel/UC']"));
for (const a of anchors) {
  const href = a.getAttribute("href") || "";
  const m = href.match(/\\/channel\\/(UC[\\w-]+)/);
  if (m) return m[1];
}
return null;
"""
    try:
        found = driver.execute_script(script)
    except Exception:
        return None
    if isinstance(found, str) and found.startswith("UC"):
        return found
    return None


def _try_direct_upload_url(driver, by, *, timeout_sec: float, progress_cb: ProgressCallback | None) -> object | None:
    def emit(message: str) -> None:
        if progress_cb is not None:
            progress_cb(message)

    current_url = driver.current_url
    channel_id = _extract_channel_id(current_url)
    if channel_id:
        for target_url in (
            f"https://studio.youtube.com/channel/{channel_id}/videos/upload",
            f"https://studio.youtube.com/channel/{channel_id}/videos/upload?d=ud",
        ):
            emit(f"Trying direct upload URL: {target_url}")
            driver.get(target_url)
            time.sleep(1.3)
            found = _find_upload_input(driver, by, timeout_sec=timeout_sec)
            if found is not None:
                return found
    return None


def _capture_debug_bundle(driver, *, reason: str, debug_root: Path | None = None) -> Path | None:
    root = debug_root or (Path.cwd() / "logs" / "roxy_debug")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    safe_reason = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-") or "failure"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bundle = root / f"{stamp}-{safe_reason}"
    try:
        bundle.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    page_url = ""
    page_title = ""
    page_source = ""
    try:
        page_url = driver.current_url
    except Exception:
        page_url = ""
    try:
        page_title = driver.title
    except Exception:
        page_title = ""
    try:
        page_source = driver.page_source or ""
    except Exception:
        page_source = ""

    context_lines = [
        f"time={datetime.now().isoformat(timespec='seconds')}",
        f"reason={reason}",
        f"url={page_url}",
        f"title={page_title}",
    ]
    try:
        (bundle / "context.txt").write_text("\n".join(context_lines), encoding="utf-8")
    except OSError:
        pass
    try:
        (bundle / "page_source.html").write_text(page_source, encoding="utf-8")
    except OSError:
        pass
    try:
        driver.save_screenshot(str(bundle / "screenshot.png"))
    except Exception:
        pass
    return bundle


def _prepare_youtube_upload_page(
    driver,
    by,
    *,
    progress_cb: ProgressCallback | None,
) -> object:
    def emit(message: str) -> None:
        if progress_cb is not None:
            progress_cb(message)

    emit("Opening YouTube Studio...")
    driver.get("https://studio.youtube.com")
    time.sleep(2.0)
    current_url = driver.current_url.lower()
    if "accounts.google.com" in current_url or "youtube.com/signin" in current_url:
        raise RoxyUploadError(
            "Profile is not logged into YouTube Studio. Please sign in manually in this Roxy profile, then retry."
        )

    # Prefer direct upload URL by channel id first (more deterministic, less navigation churn).
    channel_id = _discover_channel_id(driver)
    if channel_id:
        emit(f"Resolved channel id: {channel_id} (opening upload directly)")
        target = _try_direct_upload_url(driver, by, timeout_sec=10.0, progress_cb=progress_cb)
        if target is not None:
            return target
    else:
        emit("Could not resolve channel id from current page. Falling back to Create flow.")

    emit("Trying Create -> Upload videos flow...")
    clicked_create = _click_first_visible(
        driver,
        by,
        [
            "ytcp-button#create-icon button",
            "ytcp-icon-button#create-icon",
            "button[aria-label*='Create']",
            "button[aria-label*='Tạo']",
            "tp-yt-iron-icon#create-icon",
        ],
    )
    if not clicked_create:
        emit("Create icon not found by CSS, trying text-based fallback...")
        clicked_create = _click_upload_menu_item(driver, by)
    if clicked_create:
        time.sleep(1.2)
        _click_upload_menu_item(driver, by)
        time.sleep(1.6)

    target = _find_upload_input(driver, by, timeout_sec=12.0)
    if target is None and channel_id:
        emit("Create flow did not expose upload input, retrying direct channel upload URL once...")
        target = _try_direct_upload_url(driver, by, timeout_sec=8.0, progress_cb=progress_cb)
    if target is None:
        current_url = driver.current_url
        page_title = ""
        try:
            page_title = driver.title
        except Exception:
            page_title = ""
        raise RoxyUploadError(
            "Could not find YouTube upload file input.\n"
            "Ensure this Roxy profile has access to YouTube Studio and can open the upload dialog.\n"
            f"Current page: {current_url or '(unknown)'}\n"
            f"Page title: {page_title or '(unknown)'}"
        )
    return target


def upload_video_via_roxy(
    *,
    api_host: str,
    api_token: str,
    workspace_id: int,
    profile_id: str,
    video_path: Path,
    close_profile_after_start: bool = False,
    progress_cb: ProgressCallback | None = None,
    debug_root: Path | None = None,
) -> RoxyUploadSummary:
    """Open a Roxy profile and start YouTube Studio upload by selecting the video file."""
    if not profile_id.strip():
        raise RoxyUploadError("Roxy profile ID is required.")
    if workspace_id <= 0:
        raise RoxyUploadError("Workspace ID must be greater than 0.")
    if not api_token.strip():
        raise RoxyUploadError("Roxy API key/token is required.")
    if not video_path.exists() or not video_path.is_file():
        raise RoxyUploadError(f"Video file does not exist: {video_path}")

    def emit(message: str) -> None:
        logger.info(message)
        if progress_cb is not None:
            progress_cb(message)

    client = RoxyApiClient(api_host, api_token)

    emit("Opening Roxy profile...")
    open_data = client.open_profile(
        workspace_id=workspace_id,
        profile_id=profile_id.strip(),
        force_open=True,
    )
    debugger_address = _normalize_debugger_address(str(open_data.get("http") or ""))
    driver_path = str(open_data.get("driver") or "").strip()

    if not debugger_address:
        raise RoxyUploadError("Roxy did not return a debugger address (`data.http`).")
    if not driver_path:
        raise RoxyUploadError("Roxy did not return a Selenium driver path (`data.driver`).")

    webdriver, service_cls, by = _require_selenium()

    emit("Connecting Selenium to opened Roxy browser...")
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_experimental_option("debuggerAddress", debugger_address)
    chrome_service = service_cls(driver_path)
    try:
        driver = webdriver.Chrome(service=chrome_service, options=chrome_options)
    except Exception as exc:
        raise RoxyUploadError(
            "Could not attach Selenium to the opened Roxy browser. "
            "Ensure the profile is open and the provided driver path is valid."
        ) from exc

    try:
        file_input = _prepare_youtube_upload_page(driver, by, progress_cb=progress_cb)
        resolved_path = str(video_path.resolve())
        emit(f"Selecting video file: {video_path.name}")
        file_input.send_keys(resolved_path)
        time.sleep(1.2)
        emit("Video file submitted to YouTube Studio upload dialog.")
    except Exception as exc:
        bundle = _capture_debug_bundle(driver, reason="youtube-upload", debug_root=debug_root)
        if bundle is not None:
            emit(f"Debug bundle saved: {bundle}")
        message = str(exc)
        if bundle is not None:
            message = f"{message}\nDebug bundle: {bundle}"
        try:
            driver.service.stop()
        except Exception:
            pass
        raise RoxyUploadError(message) from exc
    else:
        # Stop chromedriver process while keeping the browser/profile open for manual handling.
        try:
            driver.service.stop()
        except Exception:
            pass

    if close_profile_after_start:
        emit("Closing Roxy profile...")
        client.close_profile(profile_id.strip())

    message = (
        "Upload started: file has been pushed to YouTube Studio. "
        "If CAPTCHA/phone verification appears, complete it manually in the opened Roxy browser window."
    )
    return RoxyUploadSummary(
        profile_id=profile_id.strip(),
        video_path=video_path,
        debugger_address=debugger_address,
        message=message,
    )
