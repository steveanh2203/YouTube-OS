"""Domain models used across the AutoCapcut prototype."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class ProjectStatus(Enum):
    """High-level lifecycle of a project within the automation queue."""

    pending = auto()
    processing = auto()
    done = auto()
    failed = auto()

    def label(self) -> str:
        return self.name.capitalize()


class ProjectSource(Enum):
    """Where the project data originates from inside CapCut's storage."""

    local = "local"
    cloud_cache = "cloud_cache"

    def label(self) -> str:
        return "Local" if self is ProjectSource.local else "Cloud"


@dataclass(slots=True)
class ProjectItem:
    """Representation of a CapCut project ready for automation."""

    name: str
    path: str  # absolute folder holding the draft_content.json
    source: ProjectSource
    status: ProjectStatus = ProjectStatus.pending
    notes: str = ""
    is_selected: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def reset(self) -> None:
        self.status = ProjectStatus.pending
        self.notes = ""
        self.is_selected = False

    def refresh_metadata(self) -> dict[str, Any]:
        """Reload lightweight metadata from the project folder."""

        from autocapcut.services.project_loader import inspect_project

        info = inspect_project(self.path)
        if info:
            self.metadata.update(info)
        return info
