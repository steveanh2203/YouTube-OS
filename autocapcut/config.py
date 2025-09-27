"""Centralised configuration for the AutoCapcut prototype."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CapCutConfig:
    """Basic configuration options for interacting with CapCut."""

    project_root: Path
    local_draft_dir: str = "com.lveditor.draft"
    cloud_draft_prefix: str = "com.lveditor.cloud.draft"
    export_shortcut: tuple[str, ...] = ("shift", "e")
    focus_delay_sec: float = 0.6
    menu_delay_sec: float = 0.4
    render_timeout_sec: int = 900
    mock_mode: bool = False
    log_directory: Path = field(default_factory=lambda: Path.cwd() / "logs")


APP_CONFIG = CapCutConfig(
    project_root=(
        Path.home()
        / "Movies"
        / "CapCut"
        / "User Data"
        / "Projects"
    )
)
