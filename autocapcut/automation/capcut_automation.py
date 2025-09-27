"""macOS automation backend using Accessibility APIs."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable

import pyautogui
import subprocess
from AppKit import NSWorkspace  # type: ignore
from loguru import logger

from autocapcut.automation.base import AutomationBackend
from autocapcut.config import APP_CONFIG
from autocapcut.models import ProjectItem

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

    def focus_capcut(self) -> None:
        logger.debug("Focusing CapCut application")
        NSWorkspace.sharedWorkspace().launchApplication_("CapCut")
        time.sleep(APP_CONFIG.focus_delay_sec)

    def _get_app(self):
        if atomacos is None:
            raise RuntimeError("atomacos not installed; install dependencies and enable accessibility")
        return atomacos.getAppRefByBundleId(self.bundle_id)

    def open_project(self, project: ProjectItem) -> bool:
        """Focus CapCut and open the requested project from the home screen list."""

        try:
            self.focus_capcut()
            element = self._locate_project_element(project.name)
            if element is None:
                logger.warning("Could not locate project '%s' in CapCut home", project.name)
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
            time.sleep(2.0)
            return True
        except Exception as exc:  # pragma: no cover - GUI automation
            logger.exception("Failed to open project %s: %s", project.name, exc)
            return False

    def _locate_project_element(self, project_name: str):
        """Locate the accessibility element that contains the CapCut project title."""

        target_suffix = project_name.strip()
        search_token = f"HomePageDraftTitle:{target_suffix}"

        try:
            app = self._get_app()
        except Exception as exc:  # pragma: no cover
            logger.debug("Unable to access CapCut app: %s", exc)
            return None

        for window in app.AXWindows:
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

    def start_render(self) -> bool:
        try:
            self.focus_capcut()
            logger.info("Triggering render")
            pyautogui.hotkey(*APP_CONFIG.export_shortcut)
            if not self._wait_for_export_dialog_visible(expected=True):
                logger.error("Export dialog did not appear after invoking shortcut")
                return False
            time.sleep(0.4)
            if not self._click_export_button():
                logger.error("Export button not found; render cannot continue")
                return False
            return True
        except Exception as exc:  # pragma: no cover
            logger.exception("start_render failed: %s", exc)
            return False

    def wait_for_render_complete(self, timeout_sec: int) -> bool:
        logger.info("Waiting for render completion (timeout %ss)", timeout_sec)
        if not self._wait_for_export_dialog_visible(expected=False, timeout=timeout_sec):
            logger.error("Render did not finish before timeout")
            return False
        logger.info("Render dialog dismissed; assuming completion")
        return True

    def _click_export_button(self) -> bool:
        try:
            app = self._get_app()
        except Exception as exc:  # pragma: no cover
            logger.debug("Unable to access CapCut for export button: %s", exc)
            return False

        export_keywords = ("ExportOkBtn", "Export video", "Export", "Start export")
        for window in app.AXWindows:
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

    def _find_button(self, root, keywords: tuple[str, ...]):
        return self._find_element_with_keywords(root, keywords, roles=("AXButton",))

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
                for attr in ("AXTitle", "AXValue", "AXIdentifier", "AXDescription", "AXLabel"):
                    value = getattr(node, attr, None)
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

        try:
            app = self._get_app()
        except Exception as exc:  # pragma: no cover - tolerate lookup failures
            logger.debug("Export dialog probe failed: %s", exc)
            return False

        export_keywords = ("ExportOkBtn", "Export video", "Export", "Start export")
        cancel_keywords = ("automationcancel", "Cancel")
        label_keywords = ("ExportDialog",)

        for window in app.AXWindows:
            if self._find_button(window, export_keywords):
                return True
            if self._find_button(window, cancel_keywords):
                return True
            if self._find_element_with_keywords(window, label_keywords, roles=("AXStaticText", "AXSheet")):
                return True
        return False

