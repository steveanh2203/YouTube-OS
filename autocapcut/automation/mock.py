"""Mock automation backend for development without CapCut."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from loguru import logger

from autocapcut.automation.base import AutomationBackend
from autocapcut.models import ProjectItem


@dataclass
class MockAutomation(AutomationBackend):
    delay_sec: float = 0.2

    def focus_capcut(self) -> None:
        logger.debug("[MOCK] focus CapCut")
        time.sleep(self.delay_sec)

    def open_project(self, project: ProjectItem) -> bool:
        logger.info("[MOCK] open project %s", project.name)
        time.sleep(self.delay_sec)
        return True

    def apply_animations(self, animation_names: Iterable[str], duration_sec: float) -> bool:
        logger.info("[MOCK] apply animations %s (duration %.2f)", list(animation_names), duration_sec)
        time.sleep(self.delay_sec)
        return True

    def apply_effects(self, effect_names: Iterable[str]) -> bool:
        logger.info("[MOCK] apply effects %s", list(effect_names))
        time.sleep(self.delay_sec)
        return True

    def apply_transitions(self, transition_names: Iterable[str]) -> bool:
        logger.info("[MOCK] apply transitions %s", list(transition_names))
        time.sleep(self.delay_sec)
        return True

    def sync_audio(self) -> bool:
        logger.info("[MOCK] sync audio")
        time.sleep(self.delay_sec)
        return True

    def sync_images(self) -> bool:
        logger.info("[MOCK] sync images")
        time.sleep(self.delay_sec)
        return True

    def start_render(self) -> bool:
        logger.info("[MOCK] start render")
        time.sleep(self.delay_sec)
        return True

    def wait_for_render_complete(self, timeout_sec: int) -> bool:
        logger.info("[MOCK] wait for render complete (timeout=%s)", timeout_sec)
        time.sleep(self.delay_sec)
        return True
