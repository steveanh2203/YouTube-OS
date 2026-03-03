"""macOS automation backend using Accessibility APIs."""
from __future__ import annotations

import re
import unicodedata
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pyautogui
import subprocess
from AppKit import NSWorkspace  # type: ignore
from loguru import logger

from autocapcut.automation.base import AutomationBackend
from autocapcut.config import APP_CONFIG
from autocapcut.models import ProjectItem
from autocapcut.services.capcut_shortcuts import (
    ShortcutProfile,
    load_active_shortcut_profile,
    resolve_action_shortcut,
)

# pylint: disable=import-error,no-name-in-module
try:
    import atomacos
except Exception as exc:  # pragma: no cover - optional dependency during dev
    atomacos = None  # type: ignore
    logger.debug("atomacos unavailable: %s", exc)


@dataclass
class MacCapCutAutomation(AutomationBackend):
    """A best-effort automation client for CapCut on macOS."""

    bundle_id: str = "com.lemon.lvoverseas"
    last_export_folder: Path | None = None
    last_export_name: str | None = None
    last_export_file: Path | None = None
    _shortcut_profile: ShortcutProfile | None = field(default=None, init=False, repr=False)
    _shortcut_cache: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)

    def focus_capcut(self) -> None:
        logger.debug("Focusing CapCut application")
        NSWorkspace.sharedWorkspace().launchApplication_("CapCut")
        time.sleep(APP_CONFIG.focus_delay_sec)

    def _get_app(self):
        if atomacos is None:
            raise RuntimeError("atomacos not installed; install dependencies and enable accessibility")
        return atomacos.getAppRefByBundleId(self.bundle_id)

    def _iter_ui_roots(self) -> list:
        """Safely return traversable UI roots even when AXWindows is unavailable."""
        try:
            app = self._get_app()
        except Exception as exc:
            logger.debug("Unable to access CapCut app: %s", exc)
            return []

        roots: list = []
        visited: set[int] = set()

        def add_node(node) -> None:
            if node is None:
                return
            ref = getattr(node, "ref", None)
            identifier = id(ref) if ref is not None else id(node)
            if identifier in visited:
                return
            visited.add(identifier)
            roots.append(node)

        for attr in ("AXWindows", "AXMainWindow", "AXFocusedWindow"):
            try:
                value = getattr(app, attr)
            except Exception:
                continue
            if not value:
                continue
            if isinstance(value, (list, tuple)):
                for node in value:
                    add_node(node)
            else:
                add_node(value)

        if not roots:
            add_node(app)
        return roots

    def _user_data_dir(self) -> Path:
        # APP_CONFIG.project_root points to ".../User Data/Projects"
        return APP_CONFIG.project_root.parent

    def _ensure_shortcuts_loaded(self) -> None:
        if self._shortcut_profile is not None:
            return

        profile = load_active_shortcut_profile(self._user_data_dir())
        self._shortcut_profile = profile
        self._shortcut_cache = {
            "exportVideo": resolve_action_shortcut(profile, "exportVideo", APP_CONFIG.export_shortcut),
            "closeDialog": resolve_action_shortcut(profile, "closeDialog", ("escape",)),
            "closeWindow": resolve_action_shortcut(profile, "closeWindow", ("command", "w")),
        }

        if profile.name:
            logger.info(
                "Loaded CapCut shortcuts profile '%s' from %s",
                profile.name,
                profile.source or "(unknown source)",
            )
        else:
            logger.warning("Could not detect active CapCut shortcut profile; using defaults.")

    def _action_shortcut(self, action_name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        self._ensure_shortcuts_loaded()
        return self._shortcut_cache.get(action_name, fallback)

    @staticmethod
    def _send_shortcut(keys: tuple[str, ...]) -> None:
        if not keys:
            return
        if len(keys) == 1:
            pyautogui.press(keys[0])
            return
        pyautogui.hotkey(*keys)

    def open_project(self, project: ProjectItem) -> bool:
        """Focus CapCut, confirm dashboard readiness, then click the project."""
        try:
            self.focus_capcut()
            if not self.wait_for_dashboard_ready(APP_CONFIG.dashboard_ready_timeout_sec):
                logger.warning("CapCut dashboard is not ready; cannot click project '%s'.", project.name)
                return False

            element = self._locate_project_element(project.name)
            if element is None:
                logger.warning("Could not locate project '%s' in CapCut home.", project.name)
                return False

            frame = element.AXFrame
            center_x = frame.x + frame.width / 2
            center_y = frame.y + frame.height / 2
            logger.debug(
                "Clicking project '%s' at screen position (%.1f, %.1f)",
                project.name,
                center_x,
                center_y,
            )
            pyautogui.moveTo(center_x, center_y, duration=0.15)
            pyautogui.doubleClick()
            time.sleep(0.25)
            return True
        except Exception as exc:  # pragma: no cover - GUI automation
            logger.exception("Failed to click project %s: %s", project.name, exc)
            return False

    def _dashboard_visible(self) -> bool:
        """Return True when the CapCut home/dashboard screen is visible."""
        try:
            for window in self._iter_ui_roots():
                queue = deque([window])
                visited: set[int] = set()
                while queue:
                    node = queue.popleft()
                    ref = getattr(node, "ref", None)
                    identifier = id(ref) if ref is not None else id(node)
                    if identifier in visited:
                        continue
                    visited.add(identifier)
                    try:
                        value = node.AXValue
                    except Exception:
                        value = None
                    if isinstance(value, str) and "HomePageDraftTitle:" in value:
                        return True
                    try:
                        children = node.AXChildren
                    except Exception:
                        children = []
                    for child in children:
                        queue.append(child)
        except Exception as exc:
            logger.debug("_dashboard_visible poll error: %s", exc)
        return False

    def wait_for_dashboard_ready(self, timeout_sec: int) -> bool:
        """Poll until the CapCut dashboard/home screen is visible."""
        logger.info("Waiting for CapCut dashboard to be ready (timeout=%ds)…", timeout_sec)
        deadline = time.time() + max(timeout_sec, 1)
        while time.time() < deadline:
            if self._dashboard_visible():
                logger.info("Dashboard detected — home screen is ready.")
                return True
            time.sleep(0.2)
        logger.warning("Dashboard not detected within %ds.", timeout_sec)
        return False

    def _close_export_dialog(self) -> bool:
        """Dismiss the export dialog without starting export."""
        if not self._export_dialog_visible():
            return True

        close_dialog_shortcut = self._action_shortcut("closeDialog", ("escape",))
        for _ in range(4):
            self._send_shortcut(close_dialog_shortcut)
            time.sleep(0.15)
            if not self._export_dialog_visible():
                return True
            pyautogui.press("escape")
            time.sleep(0.15)
            if not self._export_dialog_visible():
                return True
        return not self._export_dialog_visible()

    def _probe_export_dialog_available(self, timeout: float = 2.5) -> bool:
        """Use the export dialog as a safe readiness probe for the editor."""
        export_shortcut = self._action_shortcut("exportVideo", APP_CONFIG.export_shortcut)
        if not self._open_export_dialog_resilient(export_shortcut, timeout=max(timeout, 0.5)):
            return False
        logger.debug("Editor readiness probe opened the export dialog.")
        return self._close_export_dialog()

    def _project_editor_ready(self) -> bool:
        """Best-effort editor detection before attempting export."""
        if self._dashboard_visible():
            return False
        if self._is_link_media_dialog_visible():
            return False
        if self._export_dialog_visible():
            return True
        return False

    def _recover_project_open(self, project: ProjectItem) -> bool:
        """Recover from a stuck project-open attempt by returning to dashboard and retrying."""
        logger.warning("Attempting project-open recovery for '%s'.", project.name)
        try:
            self.focus_capcut()
            self.dismiss_dialogs(assume_focused=True)
            if self._export_dialog_visible():
                self._close_export_dialog()
            if not self._dashboard_visible():
                self.close_project()
            if not self.wait_for_dashboard_ready(APP_CONFIG.dashboard_ready_timeout_sec):
                logger.warning("Recovery could not restore the dashboard before reopening '%s'.", project.name)
                return False
            return self.open_project(project)
        except Exception as exc:
            logger.exception("Project-open recovery failed for %s: %s", project.name, exc)
            return False

    def wait_for_project_editor_ready(self, project: ProjectItem, timeout_sec: int) -> bool:
        """Wait for the clicked project to finish loading into the editor."""
        total_attempts = max(APP_CONFIG.project_open_retry_count, 0) + 1

        for attempt in range(1, total_attempts + 1):
            logger.info(
                "Waiting for project editor to load for '%s' (attempt %d/%d, timeout=%ds).",
                project.name,
                attempt,
                total_attempts,
                timeout_sec,
            )
            deadline = time.time() + max(timeout_sec, 1)
            next_probe_at = time.time()
            while time.time() < deadline:
                if self._is_link_media_dialog_visible():
                    logger.warning("Link media popup detected while waiting for editor; dismissing.")
                    self._dismiss_link_media_dialog()
                    time.sleep(0.2)
                    continue

                if self._project_editor_ready():
                    logger.info("Project editor detected for '%s'.", project.name)
                    return True

                now = time.time()
                if not self._dashboard_visible() and now >= next_probe_at:
                    probe_timeout = min(2.5, max(0.5, deadline - time.time()))
                    if self._probe_export_dialog_available(timeout=probe_timeout):
                        logger.info("Project editor is ready for '%s'.", project.name)
                        return True
                    next_probe_at = time.time() + 1.2

                time.sleep(0.35)

            if attempt >= total_attempts:
                logger.warning(
                    "Project editor not ready within %ds for '%s' after %d attempt(s).",
                    timeout_sec,
                    project.name,
                    attempt,
                )
                return False

            logger.warning(
                "Project editor timed out for '%s' on attempt %d/%d; retrying project open.",
                project.name,
                attempt,
                total_attempts,
            )
            if not self._recover_project_open(project):
                logger.warning("Project-open recovery failed for '%s'.", project.name)
                return False

        return False

    def _locate_project_element(self, project_name: str):
        """Locate the accessibility element that contains the CapCut project title."""

        target_suffix = project_name.strip()
        search_token = f"HomePageDraftTitle:{target_suffix}"

        for window in self._iter_ui_roots():
            try:
                queue = deque([window])
            except Exception:
                continue
            visited: set[int] = set()
            while queue:
                node = queue.popleft()
                ref = getattr(node, "ref", None)
                identifier = id(ref) if ref is not None else id(node)
                if identifier in visited:
                    continue
                visited.add(identifier)

                try:
                    value = node.AXValue
                except Exception:
                    value = None
                if isinstance(value, str) and value:
                    if value == search_token or value.endswith(target_suffix):
                        return node

                try:
                    children = node.AXChildren
                except Exception:
                    children = []
                for child in children:
                    queue.append(child)

        return None

    def apply_animations(self, animation_names: Iterable[str], duration_sec: float) -> bool:
        try:
            self.focus_capcut()
            logger.info("Applying animations %s (duration %.2f)", list(animation_names), duration_sec)
            # Placeholder: send command to open animation panel
            pyautogui.hotkey("shift", "a")  # sample hotkey (user must adjust)
            time.sleep(0.4)
            for name in animation_names:
                pyautogui.typewrite(name)
                time.sleep(0.2)
                pyautogui.press("enter")
            return True
        except Exception as exc:  # pragma: no cover
            logger.exception("apply_animations failed: %s", exc)
            return False

    def apply_effects(self, effect_names: Iterable[str]) -> bool:
        try:
            self.focus_capcut()
            logger.info("Applying effects %s", list(effect_names))
            pyautogui.hotkey("shift", "e")
            time.sleep(0.3)
            for name in effect_names:
                pyautogui.typewrite(name)
                time.sleep(0.2)
                pyautogui.press("enter")
            return True
        except Exception as exc:  # pragma: no cover
            logger.exception("apply_effects failed: %s", exc)
            return False

    def apply_transitions(self, transition_names: Iterable[str]) -> bool:
        try:
            self.focus_capcut()
            logger.info("Applying transitions %s", list(transition_names))
            pyautogui.hotkey("shift", "t")
            time.sleep(0.3)
            for name in transition_names:
                pyautogui.typewrite(name)
                time.sleep(0.2)
                pyautogui.press("enter")
            return True
        except Exception as exc:  # pragma: no cover
            logger.exception("apply_transitions failed: %s", exc)
            return False

    def sync_audio(self) -> bool:
        try:
            self.focus_capcut()
            logger.info("Syncing audio")
            pyautogui.hotkey("shift", "command", "s")
            time.sleep(1.0)
            return True
        except Exception as exc:  # pragma: no cover
            logger.exception("sync_audio failed: %s", exc)
            return False

    def sync_images(self) -> bool:
        try:
            self.focus_capcut()
            logger.info("Syncing images")
            pyautogui.hotkey("shift", "command", "i")
            time.sleep(1.0)
            return True
        except Exception as exc:  # pragma: no cover
            logger.exception("sync_images failed: %s", exc)
            return False

    def dismiss_dialogs(self, assume_focused: bool = False) -> bool:
        """Dismiss any dialogs that might be open (e.g., Link media dialog).
        
        Presses Escape multiple times to ensure dialogs are closed.
        """
        try:
            if not assume_focused:
                self.focus_capcut()
            logger.info("Dismissing open dialogs (fn+Esc + Esc)")

            # Prioritize Link media popup handling.
            link_visible = self._is_link_media_dialog_visible()
            if link_visible:
                self._dismiss_link_media_dialog()

            if not link_visible:
                return True

            # Generic fallback for miscellaneous modal dialogs.
            rounds = 1
            for _ in range(rounds):
                pyautogui.hotkey("fn", "escape")
                time.sleep(0.08)
                pyautogui.press("escape")
                time.sleep(0.08)
            return not self._is_link_media_dialog_visible()
        except Exception as exc:  # pragma: no cover
            logger.exception("dismiss_dialogs failed: %s", exc)
            return False

    def _open_export_dialog_resilient(self, export_shortcut: tuple[str, ...], timeout: float = 8.0) -> bool:
        """Open export dialog while aggressively dismissing Link media popups."""
        deadline = time.time() + timeout
        self._send_shortcut(export_shortcut)
        time.sleep(0.1)

        while time.time() < deadline:
            if self._is_link_media_dialog_visible():
                logger.warning("Link media popup appeared while opening export; dismissing immediately.")
                self._dismiss_link_media_dialog()
                time.sleep(0.12)
                self._send_shortcut(export_shortcut)
                time.sleep(0.1)
                continue

            if self._export_dialog_visible():
                return True

            time.sleep(0.12)

        return False

    def start_render(self, project_name: str | None = None) -> bool:
        """Start the export/render process.
        
        Flow:
        1. Dismiss any dialogs (Link media, etc.)
        2. Press Cmd+M to open export dialog
        3. Wait for dialog to appear
        4. Press Enter to confirm and start export
        """
        try:
            self.focus_capcut()
            self.last_export_folder = None
            self.last_export_name = project_name
            self.last_export_file = None
            
            # First dismiss any dialogs that might be open
            self.dismiss_dialogs(assume_focused=True)
            # Single quick re-check for delayed popup; deeper handling happens
            # in _open_export_dialog_resilient while opening export.
            time.sleep(0.1)
            if self._is_link_media_dialog_visible():
                logger.warning("Link media dialog still visible; forcing fn+Esc dismiss before export.")
                self._dismiss_link_media_dialog()
                time.sleep(0.1)
            
            # Trigger export with keyboard shortcut
            export_shortcut = self._action_shortcut("exportVideo", APP_CONFIG.export_shortcut)
            logger.info("Triggering export via shortcut: %s", "+".join(export_shortcut))
            dialog_ready = self._open_export_dialog_resilient(
                export_shortcut,
                timeout=float(APP_CONFIG.export_dialog_timeout_sec),
            )
            if not dialog_ready:
                logger.error("Export dialog not detected within %ds.", APP_CONFIG.export_dialog_timeout_sec)
                return False

            logger.info("Export dialog ready.")

            folder, name = self.read_export_destination(project_name)
            if folder is None and not name:
                logger.error("Export destination not detected; aborting render start.")
                self._close_export_dialog()
                return False

            self.last_export_folder = folder
            self.last_export_name = name or project_name
            if self.last_export_folder is None:
                logger.warning("Export folder not detected; relying on filename matching only.")
            logger.info(
                "Detected export target: folder=%s name=%s",
                self.last_export_folder,
                self.last_export_name,
            )
            
            # Press Enter to confirm export
            logger.info("Confirming export (pressing Enter)")
            pyautogui.press("enter")
            time.sleep(0.3)
            return True
        except Exception as exc:  # pragma: no cover
            logger.exception("start_render failed: %s", exc)
            return False

    def _export_start_signal(
        self,
        export_folder: str | Path | None,
        export_name: str | None,
    ) -> tuple[bool, str | None]:
        expected_path = self._resolve_expected_export_path(export_folder, export_name)
        target_name_key = self._normalize_name_key(Path(export_name).stem) if export_name else None

        if self._is_export_success_dialog_visible():
            return True, "Export completion dialog appeared."

        if expected_path is not None and expected_path.exists():
            try:
                if expected_path.is_file():
                    return True, f"Expected export file appeared: {expected_path.name}"
            except OSError:
                pass

        candidate = self._find_export_candidate(export_folder, export_name)
        if candidate is not None:
            return True, f"Matched export file candidate: {candidate.name}"

        open_files = self._get_capcut_open_files()
        if open_files:
            if expected_path is not None and str(expected_path) in open_files:
                return True, f"CapCut opened expected export file: {expected_path.name}"

            for raw_path in open_files:
                try:
                    path = Path(raw_path)
                except Exception:
                    continue
                if path.suffix.lower() not in (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".tmp"):
                    continue
                stem_key = self._normalize_name_key(path.stem)
                if target_name_key and (
                    stem_key == target_name_key
                    or stem_key.startswith(target_name_key)
                    or target_name_key.startswith(stem_key)
                ):
                    return True, f"CapCut opened matching export output: {path.name}"
                if export_folder and str(path).startswith(str(Path(export_folder).expanduser())):
                    return True, f"CapCut opened export-folder file: {path.name}"

        if not self._export_dialog_visible() and not self._dashboard_visible():
            return True, "Export dialog closed and editor remained active."

        return False, None

    def wait_for_export_started(
        self,
        timeout_sec: int,
        export_folder: str | Path | None = None,
        export_name: str | None = None,
    ) -> bool:
        """Wait until CapCut has visibly started exporting output."""
        if export_folder is None:
            export_folder = self.last_export_folder
        if export_name is None:
            export_name = self.last_export_name

        total_attempts = 2
        for attempt in range(1, total_attempts + 1):
            logger.info(
                "Waiting for export to start (attempt %d/%d, timeout=%ds).",
                attempt,
                total_attempts,
                timeout_sec,
            )
            attempt_started_at = time.time()
            deadline = time.time() + max(timeout_sec, 1)
            while time.time() < deadline:
                if self._is_link_media_dialog_visible():
                    logger.warning("Link media popup detected while waiting for export start; dismissing.")
                    self._dismiss_link_media_dialog()
                    time.sleep(0.2)
                    continue

                started, reason = self._export_start_signal(export_folder, export_name)
                if (
                    started
                    and reason == "Export dialog closed and editor remained active."
                    and (time.time() - attempt_started_at) < 1.0
                ):
                    time.sleep(0.2)
                    continue
                if started:
                    logger.info("Export start detected: %s", reason or "unknown signal")
                    return True
                time.sleep(0.25)

            if attempt >= total_attempts:
                logger.warning("Export did not start within %ds.", timeout_sec)
                return False

            logger.warning("No export-start signal detected; retrying export confirmation once.")
            if self._export_dialog_visible():
                pyautogui.press("enter")
                time.sleep(0.3)
                continue

            export_shortcut = self._action_shortcut("exportVideo", APP_CONFIG.export_shortcut)
            if not self._open_export_dialog_resilient(export_shortcut, timeout=min(5.0, float(timeout_sec))):
                logger.warning("Could not reopen export dialog for retry.")
                return False
            pyautogui.press("enter")
            time.sleep(0.3)

        return False

    def wait_for_render_complete(
        self,
        timeout_sec: int,
        export_folder: str | Path | None = None,
        export_name: str | None = None,
    ) -> bool:
        """Wait for render to complete.

        Primary path  : watchdog FSEvents (macOS native, ~0ms latency).
        Fallback path : lsof polling every 0.5s (when watchdog unavailable).
        Priority check: checks Export Complete dialog every 0.2s loop iteration.
        """
        import threading

        logger.info("⏳ Waiting for render to complete (timeout=%ds)", timeout_sec)

        if export_folder is None:
            export_folder = self.last_export_folder
        if export_name is None:
            export_name = self.last_export_name

        self.last_export_file = None
        start_time = time.time()
        last_activity_time = time.time()

        expected_path = self._resolve_expected_export_path(export_folder, export_name)
        if expected_path:
            logger.info("Expected export path: %s", expected_path)

        target_name_key: str | None = None
        if isinstance(export_name, str) and export_name.strip():
            target_name_key = self._normalize_name_key(Path(export_name).stem)

        # ── Watchdog shared state ──────────────────────────────────────────
        _detected_file: list[Path | None] = [None]
        _file_size: list[int] = [-1]
        _last_event_t: list[float] = [time.time()]
        _state_lock = threading.Lock()

        watch_dir = Path(export_folder) if export_folder else None
        if watch_dir and not watch_dir.exists():
            try:
                watch_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        def _on_fs_event(path_str: str) -> None:
            p = Path(path_str)
            try:
                if not p.is_file():
                    return
                ext = p.suffix.lower()
                if ext not in ('.mp4', '.mov', '.m4v', '.mkv', '.webm', '.tmp'):
                    return
                if any(x in path_str for x in (
                    "User Data", "Resources", ".app/", "Library/Containers", "Movies/CapCut"
                )):
                    return
                size = p.stat().st_size
            except OSError:
                return

            with _state_lock:
                cur = _detected_file[0]
                # Prioritise the exact expected file
                if expected_path is not None and p.resolve() == expected_path.resolve():
                    if size >= _file_size[0]:
                        _detected_file[0] = p
                        if size > _file_size[0]:
                            _file_size[0] = size
                            _last_event_t[0] = time.time()
                    return
                # Accept any growing video file in the folder
                if cur is None or p.resolve() == cur.resolve():
                    if size >= _file_size[0]:
                        _detected_file[0] = p
                        if size > _file_size[0]:
                            _file_size[0] = size
                            _last_event_t[0] = time.time()

        observer = None
        if watch_dir:
            try:
                from watchdog.observers.fsevents import Observer as _FSObserver
                from watchdog.events import FileSystemEventHandler as _FsHandler

                class _Handler(_FsHandler):
                    def on_created(self, event):  # noqa: D401
                        if not event.is_directory:
                            _on_fs_event(event.src_path)

                    def on_modified(self, event):  # noqa: D401
                        if not event.is_directory:
                            _on_fs_event(event.src_path)

                observer = _FSObserver()
                observer.schedule(_Handler(), str(watch_dir), recursive=False)
                observer.start()
                logger.info("📡 Watchdog FSEvents started → %s", watch_dir)
            except Exception as exc:
                logger.warning("Watchdog unavailable (%s) — falling back to lsof polling", exc)
                observer = None

        # Tuning constants
        POLL_INTERVAL   = 0.15  # Main loop: check dialog aggressively
        LSOF_INTERVAL   = 0.35  # lsof fallback poll interval
        STABLE_RELEASED = 1.2   # Watchdog: secs stable after release
        STABLE_UNKNOWN  = 3.0   # Watchdog: secs stable when open-state unknown
        GRACE_SEC       = 20    # Grace before giving up on expected_path
        LSOF_STABLE_REL = 3     # lsof fallback stable ticks when released
        LSOF_STABLE_UNK = 8     # lsof fallback stable ticks when unknown

        # lsof-fallback state
        lsof_tracked: Path | None = None
        lsof_last_size: int = -1
        lsof_stable: int = 0
        lsof_grown: bool = False
        static_files: set[str] = set()

        # Seed watchdog with expected_path if it already exists
        if expected_path and expected_path.exists():
            try:
                _on_fs_event(str(expected_path))
            except Exception:
                pass

        def _cleanup() -> None:
            if observer:
                try:
                    observer.stop()
                    observer.join(timeout=3.0)
                except Exception:
                    pass

        def _is_video_export_file(path: Path) -> bool:
            return path.suffix.lower() in (".mp4", ".mov", ".m4v", ".mkv", ".webm")

        def _candidate_from_open_files() -> Path | None:
            open_paths = self._get_capcut_open_files()
            if not open_paths:
                return None

            candidates: list[Path] = []
            for raw in open_paths:
                try:
                    path = Path(raw)
                    if not path.exists() or not path.is_file():
                        continue
                except (OSError, PermissionError):
                    continue
                if not _is_video_export_file(path):
                    continue
                candidates.append(path)

            if not candidates:
                return None

            if target_name_key:
                matched = [
                    item for item in candidates
                    if (
                        self._normalize_name_key(item.stem) == target_name_key
                        or self._normalize_name_key(item.stem).startswith(target_name_key)
                    )
                ]
                if matched:
                    return max(matched, key=lambda item: item.stat().st_mtime)

            return max(candidates, key=lambda item: item.stat().st_mtime)

        def _remember_export_file(candidate: Path | None) -> Path | None:
            chosen: Path | None = None
            if candidate is not None:
                try:
                    if candidate.exists() and candidate.is_file() and _is_video_export_file(candidate):
                        chosen = candidate.resolve()
                except OSError:
                    chosen = None

            if chosen is None and expected_path is not None:
                try:
                    if expected_path.exists() and expected_path.is_file() and _is_video_export_file(expected_path):
                        chosen = expected_path.resolve()
                except OSError:
                    chosen = None

            if chosen is None:
                fallback = self._find_export_candidate(export_folder, export_name)
                if fallback is not None:
                    chosen = fallback.resolve()

            if chosen is None:
                open_file_candidate = _candidate_from_open_files()
                if open_file_candidate is not None:
                    chosen = open_file_candidate.resolve()

            if chosen is None:
                return None

            self.last_export_file = chosen
            self.last_export_folder = chosen.parent
            if chosen.stem:
                self.last_export_name = chosen.stem
            logger.info("Remembered exported file: {}", chosen)
            return chosen

        try:
            while time.time() - last_activity_time < timeout_sec:
                elapsed = int(time.time() - start_time)

                if self._is_link_media_dialog_visible():
                    logger.warning("  ⚠️ Link media popup detected during render monitoring; dismissing.")
                    self._dismiss_link_media_dialog()
                    time.sleep(POLL_INTERVAL)
                    continue

                # ── Priority: Export Complete dialog ──────────────────────
                if self._is_export_success_dialog_visible():
                    logger.info("  ✅ Detected Export Complete dialog. Render finished.")
                    if self._finish_render():
                        remembered = _remember_export_file(
                            _detected_file[0] if _detected_file[0] is not None else lsof_tracked
                        )
                        if remembered is None:
                            logger.warning("Export complete but no output file could be remembered.")
                        return True
                    logger.warning("  ⚠️ Dialog found but could not dismiss yet.")

                # ── Watchdog path ─────────────────────────────────────────
                if observer is not None:
                    with _state_lock:
                        dfile = _detected_file[0]
                        dsize = _file_size[0]
                        dlast = _last_event_t[0]

                    if dfile is not None and dsize > 0:
                        stable_sec = time.time() - dlast
                        size_mb = dsize / (1024 * 1024)

                        # File still changing → keep inactivity timer alive
                        if stable_sec < STABLE_UNKNOWN:
                            last_activity_time = time.time()

                        if stable_sec >= STABLE_RELEASED:
                            open_files = self._get_capcut_open_files()
                            is_open: bool | None = (
                                str(dfile) in open_files if open_files else None
                            )
                            if is_open is False:
                                logger.info(
                                    "  ✅ Export complete! File released: %s (%.2f MB)",
                                    dfile.name, size_mb,
                                )
                                if self._finish_render():
                                    _remember_export_file(dfile)
                                    return True
                            elif is_open is None and stable_sec >= STABLE_UNKNOWN:
                                logger.info(
                                    "  ✅ Export complete (stable %.0fs): %s (%.2f MB)",
                                    stable_sec, dfile.name, size_mb,
                                )
                                if self._finish_render():
                                    _remember_export_file(dfile)
                                    return True

                        if elapsed % 5 == 0:
                            logger.info(
                                "  📝 Exporting... %.2f MB (stable %.1fs)",
                                size_mb, time.time() - dlast,
                            )
                    else:
                        # No file detected yet — also poll expected_path directly
                        if expected_path and expected_path.exists():
                            try:
                                _on_fs_event(str(expected_path))
                            except Exception:
                                pass
                        if elapsed % 5 == 0:
                            logger.info("  ⏳ Waiting for export file to appear...")

                    time.sleep(POLL_INTERVAL)
                    continue

                # ── lsof fallback path ────────────────────────────────────
                open_files_str = self._get_capcut_open_files()

                if not lsof_tracked:
                    candidates: list[Path] = []
                    for f in open_files_str:
                        try:
                            p = Path(f)
                            if not p.exists() or not p.is_file():
                                continue
                        except (OSError, PermissionError):
                            continue
                        if p.suffix.lower() not in ('.mp4', '.mov', '.m4v', '.mkv', '.webm', '.tmp'):
                            continue
                        if target_name_key:
                            stem_key = self._normalize_name_key(p.stem)
                            if stem_key == target_name_key or stem_key.startswith(target_name_key):
                                candidates.append(p)
                                continue
                        if any(x in f for x in (
                            "/System/", "/usr/", "/dev/", ".app/",
                            "User Data", "Resources", ".ttf", ".dylib",
                            "Library/Containers", "Movies/CapCut",
                        )):
                            continue
                        if f in static_files:
                            continue
                        candidates.append(p)

                    if not candidates:
                        if elapsed % 5 == 0:
                            logger.info("  ⏳ Waiting for export start... (No candidate files)")
                    else:
                        for cand in candidates:
                            try:
                                s = cand.stat().st_size
                            except OSError:
                                continue
                            if target_name_key:
                                stem = self._normalize_name_key(cand.stem)
                                if stem == target_name_key or stem.startswith(target_name_key):
                                    lsof_tracked = cand
                                    lsof_last_size = s
                                    logger.info("  🎥 Tracking (name match): %s", cand.name)
                                    break
                            if export_folder and str(cand).startswith(str(export_folder)):
                                lsof_tracked = cand
                                lsof_last_size = s
                                logger.info("  🎥 Tracking (folder match): %s", cand.name)
                                break
                        if not lsof_tracked:
                            lsof_tracked = candidates[0]
                            try:
                                lsof_last_size = lsof_tracked.stat().st_size
                            except OSError:
                                lsof_last_size = 0
                            logger.info("  🎥 Monitoring: %s", lsof_tracked.name)
                else:
                    is_open_lsof: bool | None = (
                        str(lsof_tracked) in open_files_str if open_files_str else None
                    )
                    try:
                        cur_size = lsof_tracked.stat().st_size
                    except OSError:
                        logger.warning("  ⚠️ Tracked file gone. Re-detecting.")
                        lsof_tracked = None
                        lsof_grown = False
                        lsof_stable = 0
                        time.sleep(LSOF_INTERVAL)
                        continue

                    size_mb = cur_size / (1024 * 1024)

                    if is_open_lsof is False:
                        logger.info(
                            "  ✅ File released: %s (%.2f MB)", lsof_tracked.name, size_mb
                        )
                        if self._finish_render():
                            _remember_export_file(lsof_tracked)
                            return True

                    if cur_size > lsof_last_size:
                        lsof_grown = True
                        lsof_stable = 0
                        last_activity_time = time.time()
                        if elapsed % 3 == 0:
                            logger.info("  📝 Exporting... %.2f MB (Growing)", size_mb)
                    elif is_open_lsof is True:
                        lsof_stable = 0
                    else:
                        lsof_stable += 1
                        required = LSOF_STABLE_REL if is_open_lsof is False else LSOF_STABLE_UNK
                        if lsof_grown and lsof_stable >= required and cur_size > 0:
                            logger.info(
                                "  ✅ Export complete (stable): %s (%.2f MB)",
                                lsof_tracked.name, size_mb,
                            )
                            if self._finish_render():
                                _remember_export_file(lsof_tracked)
                                return True
                        is_video = lsof_tracked.suffix.lower() in (
                            '.mp4', '.mov', '.m4v', '.mkv', '.webm'
                        )
                        if not lsof_grown and not is_video and lsof_stable > 10:
                            logger.warning(
                                "  ⚠️ %s not growing for 5s. Ignoring as static.",
                                lsof_tracked.name,
                            )
                            static_files.add(str(lsof_tracked))
                            lsof_tracked = None
                            lsof_stable = 0

                    lsof_last_size = cur_size

                time.sleep(LSOF_INTERVAL)

        finally:
            _cleanup()

        logger.warning("  ❌ Timeout reached.")
        self._finish_render()
        return False

    def _get_capcut_open_files(self) -> list[str]:
        """Run lsof on ALL CapCut processes (including helper/renderer)."""
        import subprocess
        try:
            # 1. Get PIDs of everything 'CapCut'
            # pgrep -f matches full command line (case insensitive)
            pid_cmd = "pgrep -f -i CapCut"
            pid_res = subprocess.run(pid_cmd, shell=True, text=True, capture_output=True)
            if pid_res.returncode != 0:
                return []
            
            pids = [p.strip() for p in pid_res.stdout.splitlines() if p.strip()]
            if not pids:
                return []
                
            pid_list = ",".join(pids)
            
            # 2. lsof -p PID,PID,...
            # -F n: output names
            cmd = f"lsof -p {pid_list} -F n"
            result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
            
            # Parse
            files = []
            for line in result.stdout.splitlines():
                if line.startswith('n/'):
                    files.append(line[1:])
            return list(set(files))
        except Exception as exc:
            logger.debug("lsof error: %s", exc)
            return []

    def _wait_for_dashboard(self, timeout: float = 30.0) -> bool:
        """Backward-compatible wrapper around wait_for_dashboard_ready."""
        return self.wait_for_dashboard_ready(int(max(timeout, 1)))

    def _finish_render(self) -> bool:
        """Dismiss the export-complete / Share dialog and return to the editor."""
        self.focus_capcut()
        # If text-based detection misses, still attempt close when known sheet
        # buttons are present.
        marker_keywords = ("automationclosebtn", "automationopenfolderbtn", "publish_pc_btn")
        marker_present = any(self._find_button(window, marker_keywords) for window in self._iter_ui_roots())
        if not self._is_export_success_dialog_visible() and not marker_present:
            return True

        # Let the export-complete dialog settle briefly before dismissing.
        time.sleep(0.45)
        logger.info("Dismissing completion dialog")
        # "Cancel" closes the Share / export-done dialog without uploading.
        cancel_keywords = (
            "automationclosebtn",
            "Cancel",
            "Hủy",
            "Huỷ",
            "Đóng",
            "Close",
            "Done",
            "OK",
        )
        close_dialog_shortcut = self._action_shortcut("closeDialog", ("escape",))

        for attempt in range(2):
            # ── Strategy 1: AX Press on Cancel button (most reliable) ──────
            cancelled = False
            for window in self._iter_ui_roots():
                button = self._find_button(window, cancel_keywords)
                if button is None:
                    continue
                # Try native AX action first
                try:
                    button.Press()
                    cancelled = True
                    logger.debug("  Dismissed via AXPress (attempt %d)", attempt + 1)
                    break
                except Exception:
                    pass
                # Fallback: pyautogui click at button coordinates
                try:
                    frame = button.AXFrame
                    cx = frame.x + frame.width / 2
                    cy = frame.y + frame.height / 2
                    pyautogui.moveTo(cx, cy, duration=0.1)
                    pyautogui.click()
                    cancelled = True
                    logger.debug("  Dismissed via pyautogui click (attempt %d)", attempt + 1)
                    break
                except Exception:
                    pass
                break  # tried this window; move on

            if cancelled:
                time.sleep(0.2)
                if not self._is_export_success_dialog_visible():
                    return True

            # ── Strategy 2: active closeDialog shortcut from CapCut keymap ─
            self._send_shortcut(close_dialog_shortcut)
            time.sleep(0.2)
            if not self._is_export_success_dialog_visible():
                return True

            # ── Strategy 3: fn+Esc fallback ────────────────────────────────
            pyautogui.hotkey("fn", "escape")
            time.sleep(0.2)
            if not self._is_export_success_dialog_visible():
                return True

            # ── Strategy 4: plain Escape ───────────────────────────────────
            pyautogui.press("escape")
            time.sleep(0.2)
            if not self._is_export_success_dialog_visible():
                return True

        logger.warning("Completion dialog still visible after all dismiss attempts.")
        return not self._is_export_success_dialog_visible()

    def _get_video_files(self, folder) -> set:
        """Get set of video file paths in folder."""
        from pathlib import Path
        folder = Path(folder)
        video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}
        result = set()
        try:
            for f in folder.iterdir():
                if f.is_file() and f.suffix.lower() in video_extensions:
                    result.add(f)
        except OSError:
            pass
        return result

    def _is_export_complete(self) -> bool:
        """Check if export has completed - DEPRECATED, kept for compatibility."""
        # This method is no longer used since we switched to file-based detection
        return False

    def close_project(self) -> bool:
        """Close current project and return to dashboard.

        Uses the active CapCut ``closeWindow`` shortcut then waits until the
        home/dashboard screen is visible before returning.
        """
        try:
            self.focus_capcut()

            for attempt in range(1, 3):
                if self._is_export_success_dialog_visible():
                    logger.info("Share dialog visible (attempt %d) — dismissing first", attempt)
                    if not self._finish_render():
                        logger.warning("Share dialog still visible after dismiss attempt %d.", attempt)
                        time.sleep(0.2)
                        continue
                    time.sleep(0.15)

                close_window_shortcut = self._action_shortcut("closeWindow", ("command", "w"))
                logger.info(
                    "Closing project via shortcut %s, attempt %d",
                    "+".join(close_window_shortcut),
                    attempt,
                )
                self._send_shortcut(close_window_shortcut)
                time.sleep(0.45)

                # Dismiss "save changes?" if it appears
                pyautogui.press("escape")
                time.sleep(0.12)

                if self.wait_for_dashboard_ready(APP_CONFIG.dashboard_ready_timeout_sec):
                    logger.info("Project closed, returned to dashboard")
                    return True

            logger.warning("Could not confirm dashboard after closing project.")
            return False
        except Exception as exc:  # pragma: no cover
            logger.exception("close_project failed: %s", exc)
            return False

    def _click_export_button(self) -> bool:
        export_keywords = ("ExportOkBtn", "Export video", "Export", "Start export")
        for window in self._iter_ui_roots():
            button = self._find_button(window, export_keywords)
            if button is None:
                continue
            try:
                frame = button.AXFrame
                center_x = frame.x + frame.width / 2
                center_y = frame.y + frame.height / 2
                pyautogui.moveTo(center_x, center_y, duration=0.15)
                pyautogui.click()
                time.sleep(0.3)
                return True
            except Exception as exc:
                logger.debug("Failed to click export button: %s", exc)
        return False

    def _click_button_by_keywords(self, keywords: tuple[str, ...]) -> bool:
        for window in self._iter_ui_roots():
            button = self._find_button(window, keywords)
            if button is None:
                continue
            try:
                button.Press()
                return True
            except Exception:
                pass
            try:
                frame = button.AXFrame
                center_x = frame.x + frame.width / 2
                center_y = frame.y + frame.height / 2
                pyautogui.moveTo(center_x, center_y, duration=0.15)
                pyautogui.click()
                return True
            except Exception:
                continue
        return False

    def _find_button(self, root, keywords: tuple[str, ...]):
        return self._find_element_with_keywords(root, keywords, roles=("AXButton",))

    @staticmethod
    def _safe_ax_attr(node, attr: str):
        try:
            return getattr(node, attr)
        except Exception:
            return None

    def _find_element_with_keywords(self, root, keywords: tuple[str, ...], roles: tuple[str, ...] | None = None):
        try:
            queue = deque([root])
        except Exception:
            return None
        visited: set[int] = set()
        lowered = tuple(keyword.lower() for keyword in keywords)
        while queue:
            node = queue.popleft()
            ref = getattr(node, 'ref', None)
            identifier = id(ref) if ref is not None else id(node)
            if identifier in visited:
                continue
            visited.add(identifier)

            try:
                role = node.AXRole
            except Exception:
                role = None
            if roles is None or role in roles:
                texts: list[str] = []
                # Skip unstable attributes and guard each lookup to avoid AX
                # errors slowing down traversal in large UI trees.
                for attr in ("AXTitle", "AXValue", "AXIdentifier", "AXLabel"):
                    value = self._safe_ax_attr(node, attr)
                    if isinstance(value, str) and value:
                        texts.append(value.lower())
                joined = " ".join(texts)
                if joined and any(keyword in joined for keyword in lowered):
                    return node

            children: list = []
            for attr in ("AXChildren", "AXSheets"):
                try:
                    value = getattr(node, attr)
                except Exception:
                    continue
                if not value:
                    continue
                if isinstance(value, (list, tuple)):
                    children.extend(value)
                else:
                    children.append(value)
            for child in children:
                queue.append(child)
        return None

    def _is_export_success_dialog_visible(self) -> bool:
        """Check if the export-complete / Share dialog is visible.

        Uses *only* highly-specific indicators to avoid false positives from
        CapCut's main editor UI (which also contains YouTube/TikTok labels).
        """
        # 1. Window title – CapCut names the dialog "Export-<project name>"
        for window in self._iter_ui_roots():
            try:
                title = getattr(window, "AXTitle", None) or ""
                if title.lower().startswith("export") and len(title) > len("export"):
                    return True
            except Exception:
                pass

        # 1.5. Strong AX button identifiers found in CapCut export-complete sheet.
        # These are the most stable markers in real-world runs:
        # - automationcloseBtn (Cancel)
        # - automationopenFolderBtn (Open folder)
        # - publish_pc_btn (Share)
        marker_keywords = (
            "automationclosebtn",
            "automationopenfolderbtn",
            "publish_pc_btn",
            "openfolder",
        )
        for window in self._iter_ui_roots():
            if self._find_button(window, marker_keywords):
                return True

        # 2. Buttons/text that are unique to the export-done dialog.
        #    "Open folder" / "Mở thư mục" only appear in this dialog.
        #    "Video is saved" is the body text of the Share dialog.
        specific_keywords = (
            "Open folder",      # English export-done dialog
            "openfolder",       # identifier variant without space
            "automationopenfolderbtn",
            "automationclosebtn",
            "publish_pc_btn",
            "Mở thư mục",       # Vietnamese "Open folder"
            "Video is saved",   # Share dialog body text
            "đã được lưu",      # Vietnamese "has been saved"
        )
        for window in self._iter_ui_roots():
            if self._find_button(window, specific_keywords):
                return True
            if self._find_element_with_keywords(
                window, specific_keywords, roles=("AXStaticText", "AXSheet")
            ):
                return True

        return False

    def _is_link_media_dialog_visible(self) -> bool:
        """Detect CapCut's 'Link media' missing-file popup."""
        title_keywords = ("link media", "liên kết media", "media lost")
        body_keywords = (
            "couldn",
            "couldn't find some of the imported media files",
            "imported media files",
            "link the media files then edit",
            "link media when selecting a folder",
            "media files then edit",
        )
        cancel_keywords = ("automationcancel", "cancel", "hủy", "huỷ", "đóng")
        link_btn_keywords = ("link media", "linkmedia", "automationlink", "link")

        for window in self._iter_ui_roots():
            try:
                title = (getattr(window, "AXTitle", None) or "").casefold()
                if any(k in title for k in title_keywords):
                    return True
            except Exception:
                pass

            if self._find_button(window, cancel_keywords) and self._find_button(window, link_btn_keywords):
                return True
            if self._find_element_with_keywords(window, body_keywords, roles=("AXStaticText", "AXSheet")):
                return True
        return False

    def _dismiss_link_media_dialog(self) -> bool:
        """Close Link media popup immediately (prefers fn+Esc)."""
        if not self._is_link_media_dialog_visible():
            return True

        logger.info("Dismissing Link media popup (fn+Esc priority)")
        cancel_keywords = ("automationcancel", "cancel", "hủy", "huỷ", "đóng", "close")

        for _ in range(4):
            pyautogui.hotkey("fn", "escape")
            time.sleep(0.2)
            if not self._is_link_media_dialog_visible():
                return True

            if self._click_button_by_keywords(cancel_keywords):
                time.sleep(0.25)
                if not self._is_link_media_dialog_visible():
                    return True

            pyautogui.press("escape")
            time.sleep(0.2)
            if not self._is_link_media_dialog_visible():
                return True

        logger.warning("Link media popup is still visible after dismiss attempts.")
        return not self._is_link_media_dialog_visible()

    def _wait_for_export_dialog_visible(self, expected: bool, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            visible = self._export_dialog_visible()
            if expected and visible:
                return True
            if not expected and not visible:
                return True
            time.sleep(0.5)
        return False

    def _export_dialog_visible(self) -> bool:
        """Best-effort detection of CapCut's export window."""
        if self._is_link_media_dialog_visible():
            return False

        export_keywords = (
            "ExportOkBtn",
            "Export video",
            "Export",
            "Start export",
            "Share",
            "Open folder",
            "Xuất",
            "Xuất video",
            "Chia sẻ",
            "Mở thư mục",
        )
        label_keywords = ("ExportDialog", "Export", "Xuất", "Share")

        for window in self._iter_ui_roots():
            if self._find_button(window, export_keywords):
                return True
            if self._find_element_with_keywords(window, label_keywords, roles=("AXStaticText", "AXSheet")):
                return True
        return False

    def _get_export_dialog_root(self):
        if self._is_link_media_dialog_visible():
            return None
        export_keywords = (
            "ExportOkBtn",
            "Export video",
            "Export",
            "Start export",
            "Share",
            "Open folder",
            "Xuất",
            "Xuất video",
            "Xuất bản",
            "Xuất file",
            "Chia sẻ",
            "Mở thư mục",
        )
        label_keywords = ("ExportDialog", "Export", "Xuất", "Share")

        for window in self._iter_ui_roots():
            if self._find_button(window, export_keywords):
                return window
            if self._find_element_with_keywords(window, label_keywords, roles=("AXStaticText", "AXSheet")):
                return window
        return None

    def _collect_text_fields(self, root) -> list:
        fields = []
        roles = ("AXTextField", "AXComboBox", "AXTextArea", "AXStaticText")
        try:
            queue = deque([root])
        except Exception:
            return fields
        visited: set[int] = set()
        while queue:
            node = queue.popleft()
            ref = getattr(node, "ref", None)
            identifier = id(ref) if ref is not None else id(node)
            if identifier in visited:
                continue
            visited.add(identifier)

            try:
                role = node.AXRole
            except Exception:
                role = None
            if role in roles:
                fields.append(node)

            children: list = []
            for attr in ("AXChildren", "AXSheets"):
                try:
                    value = getattr(node, attr)
                except Exception:
                    continue
                if not value:
                    continue
                if isinstance(value, (list, tuple)):
                    children.extend(value)
                else:
                    children.append(value)
            for child in children:
                queue.append(child)
        return fields

    @staticmethod
    def _extract_field_value(node) -> str | None:
        for attr in ("AXValue", "AXTitle", "AXDescription", "AXLabel"):
            value = getattr(node, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _parse_export_folder_value(value: str | None) -> Path | None:
        if not value:
            return None
        cleaned = value.strip()
        if cleaned.startswith("file://"):
            cleaned = cleaned.replace("file://", "", 1)
        if not ("/" in cleaned or cleaned.startswith("~")):
            return None
        path = Path(cleaned).expanduser()
        if path.is_dir():
            return path
        if path.suffix and path.parent.is_dir():
            return path.parent
        return None

    @staticmethod
    def _looks_like_name(value: str) -> bool:
        if not value:
            return False
        cleaned = value.strip()
        if "/" in cleaned:
            return False
        if re.fullmatch(r"\d{2,6}", cleaned):
            return False
        if re.fullmatch(r"\d{2,5}x\d{2,5}", cleaned.lower()):
            return False
        if cleaned.lower().endswith("fps"):
            return False
        if cleaned.lower().endswith("p") and cleaned[:-1].isdigit():
            return False
        return True

    @staticmethod
    def _normalize_text(value: str) -> str:
        return unicodedata.normalize("NFD", value).casefold().strip()

    @staticmethod
    def _normalize_name_key(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value)
        stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        cleaned = re.sub(r"[^0-9a-z]+", "", stripped.casefold())
        return cleaned

    def _resolve_expected_export_path(
        self,
        export_folder: str | Path | None,
        export_name: str | None,
    ) -> Path | None:
        if not export_folder or not export_name:
            return None
        folder = Path(export_folder).expanduser()
        name = export_name.strip()
        if not name:
            return None
        if name.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            filename = name
        else:
            filename = f"{name}.mp4"
        return folder / filename

    def _find_export_candidate(
        self,
        export_folder: str | Path | None,
        export_name: str | None,
    ) -> Path | None:
        if not export_folder or not export_name:
            return None
        folder = Path(export_folder).expanduser()
        if not folder.is_dir():
            return None
        target = self._normalize_name_key(Path(export_name).stem)
        matches: list[Path] = []
        for entry in folder.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
                continue
            stem = self._normalize_name_key(entry.stem)
            if stem == target or stem.startswith(target) or target.startswith(stem):
                matches.append(entry)
        if not matches:
            return None
        return max(matches, key=lambda p: p.stat().st_mtime)

    def _read_export_destination_from_root(
        self,
        root,
        fallback_name: str | None,
    ) -> tuple[Path | None, str | None]:
        export_folder: Path | None = None
        export_name: str | None = None

        export_field = self._find_element_with_keywords(
            root,
            (
                "export to",
                "destination",
                "save to",
                "output folder",
                "output",
                "save location",
                "folder",
                "path",
                "xuất đến",
                "lưu vào",
                "thư mục",
                "vị trí",
                "đường dẫn",
            ),
            roles=("AXTextField", "AXComboBox", "AXStaticText"),
        )
        if export_field is not None:
            export_folder = self._parse_export_folder_value(self._extract_field_value(export_field))

        name_field = self._find_element_with_keywords(
            root,
            ("name", "file name", "filename", "tên", "tên file", "tên tệp"),
            roles=("AXTextField", "AXComboBox", "AXStaticText"),
        )
        if name_field is not None:
            value = self._extract_field_value(name_field)
            if value and self._looks_like_name(value):
                export_name = value.strip()

        open_folder_button = self._find_button(
            root,
            ("open folder", "open", "folder", "mở thư mục", "mở", "thư mục"),
        )
        if open_folder_button is not None and export_folder is None:
            export_folder = self._parse_export_folder_value(self._extract_field_value(open_folder_button))

        fields = self._collect_text_fields(root)
        for field in fields:
            value = self._extract_field_value(field)
            if not value:
                continue
            if export_folder is None:
                export_folder = self._parse_export_folder_value(value)
                if export_folder is not None and export_name is None and value.lower().endswith(".mp4"):
                    export_name = Path(value).stem
            if export_name is None and fallback_name:
                if self._normalize_name_key(fallback_name) in self._normalize_name_key(value):
                    export_name = value.strip()
            if export_name is None and fallback_name is None and self._looks_like_name(value):
                export_name = value.strip()

        if export_name is None:
            export_name = fallback_name

        return export_folder, export_name

    def read_export_destination(
        self,
        fallback_name: str | None = None,
    ) -> tuple[Path | None, str | None]:
        """Best-effort read of export folder/name from the export dialog."""
        dialog = self._get_export_dialog_root()
        if dialog is not None:
            return self._read_export_destination_from_root(dialog, fallback_name)

        for window in self._iter_ui_roots():
            export_folder, export_name = self._read_export_destination_from_root(window, fallback_name)
            if export_folder is not None:
                return export_folder, export_name

        return None, fallback_name
