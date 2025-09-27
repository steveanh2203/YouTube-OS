"""Automation base classes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from autocapcut.models import ProjectItem


class AutomationBackend(ABC):
    """Actions that an automation implementation must support."""

    @abstractmethod
    def focus_capcut(self) -> None:
        """Bring CapCut to the foreground."""

    @abstractmethod
    def open_project(self, project: ProjectItem) -> bool:
        """Open a specific project inside CapCut."""

    @abstractmethod
    def apply_animations(self, animation_names: Iterable[str], duration_sec: float) -> bool:
        """Apply animations to the selected clips."""

    @abstractmethod
    def apply_effects(self, effect_names: Iterable[str]) -> bool:
        """Apply effects to selected clips."""

    @abstractmethod
    def apply_transitions(self, transition_names: Iterable[str]) -> bool:
        """Insert transitions between clips."""

    @abstractmethod
    def sync_audio(self) -> bool:
        """Trigger CapCut's audio sync."""

    @abstractmethod
    def sync_images(self) -> bool:
        """Trigger CapCut's image sync."""

    @abstractmethod
    def start_render(self) -> bool:
        """Kick off the export for the active project."""

    @abstractmethod
    def wait_for_render_complete(self, timeout_sec: int) -> bool:
        """Wait for export to finish (poll UI)."""
