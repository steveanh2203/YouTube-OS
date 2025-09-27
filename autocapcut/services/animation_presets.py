"""Preset definitions for animations and transitions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from pathlib import Path

CAPCUT_CACHE = Path.home() / "Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Cache"


@dataclass(frozen=True)
class TransitionPreset:
    key: str
    name: str
    template: dict

    def formatted_template(self) -> dict:
        """Return template with cache placeholders resolved."""

        resolved = dict(self.template)
        raw_path = resolved.get("path")
        if raw_path and "{capcut_cache}" in raw_path:
            candidate = Path(str(raw_path).replace("{capcut_cache}", str(CAPCUT_CACHE)))
            if not candidate.exists():
                effect_id = resolved.get("effect_id")
                effect_dir = CAPCUT_CACHE / "effect" / (effect_id or "")
                if effect_dir.exists():
                    for child in effect_dir.iterdir():
                        if child.is_dir():
                            candidate = child
                            break
            resolved["path"] = str(candidate)
        return resolved


BLACK_FADE = TransitionPreset(
    key="black_fade",
    name="Black Fade",
    template={
        "category_id": "123456",
        "category_name": "heycan_search_transition",
        "duration": 500000,
        "effect_id": "6724239388189921806",
        "id": "TEMPLATE",
        "is_ai_transition": False,
        "is_overlap": False,
        "name": "Black Fade",
        "path": "{capcut_cache}/effect/6724239388189921806/3bca53e9f3dfa2c184fbee96438ea097",
        "platform": "all",
        "request_id": "",
        "resource_id": "6724239388189921806",
        "source_platform": 1,
        "task_id": "",
        "third_resource_id": "6724239388189921806",
        "type": "transition",
        "video_path": ""
    }
)

TRANSITION_PRESETS: Dict[str, TransitionPreset] = {preset.key: preset for preset in (BLACK_FADE,)}
