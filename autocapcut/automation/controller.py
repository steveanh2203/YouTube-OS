"""High-level orchestration of automation actions."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Callable, Iterable

from loguru import logger

from autocapcut.automation.base import AutomationBackend
from autocapcut.config import APP_CONFIG
from autocapcut.models import ProjectItem, ProjectStatus


@dataclass
class AutomationController:
    """Coordinates the automation backend with project state tracking."""

    backend: AutomationBackend
    stop_event: Event = field(default_factory=Event)

    def render_projects(
        self,
        projects: Iterable[ProjectItem],
        progress_callback: Callable[[ProjectItem], None] | None = None,
    ) -> None:
        """Open each project and export it via CapCut."""

        for project in projects:
            if self.stop_event.is_set():
                logger.warning(
                    "Automation cancelled before processing %s", project.name
                )
                break

            logger.info("Starting render for project %s", project.name)
            project.status = ProjectStatus.processing
            project.notes = ""
            self._notify(progress_callback, project)

            if self._abort_if_cancelled(project, progress_callback):
                break

            if not self.backend.open_project(project):
                self._fail(project, "Could not open project", progress_callback)
                continue

            if self._abort_if_cancelled(project, progress_callback):
                break

            if not self.backend.start_render(project.name):
                self._fail(project, "Render start failed", progress_callback)
                continue

            if self._abort_if_cancelled(project, progress_callback):
                break

            if not self.backend.wait_for_render_complete(
                APP_CONFIG.render_timeout_sec
            ):
                self._fail(project, "Render timeout", progress_callback)
                continue

            # Close project and return to dashboard for next project
            if self._abort_if_cancelled(project, progress_callback):
                break
            
            if hasattr(self.backend, 'close_project'):
                if not self.backend.close_project():
                    self._fail(project, "Could not close project and return to dashboard", progress_callback)
                    continue

            project.status = ProjectStatus.done
            project.notes = ""
            self._notify(progress_callback, project)

        logger.info("Render pipeline completed")

    def _abort_if_cancelled(
        self,
        project: ProjectItem,
        callback: Callable[[ProjectItem], None] | None,
    ) -> bool:
        if self.stop_event.is_set():
            logger.warning("Automation cancelled while processing %s", project.name)
            self._fail(project, "Cancelled by user", callback)
            return True
        return False

    @staticmethod
    def _notify(
        callback: Callable[[ProjectItem], None] | None, project: ProjectItem
    ) -> None:
        if callback is not None:
            callback(project)

    def _fail(
        self,
        project: ProjectItem,
        message: str,
        callback: Callable[[ProjectItem], None] | None,
    ) -> None:
        project.status = ProjectStatus.failed
        project.notes = message
        self._notify(callback, project)

    def cancel(self) -> None:
        self.stop_event.set()
