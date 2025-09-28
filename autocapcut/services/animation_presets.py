"""Preset definitions for animations, effects, and transitions."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

CAPCUT_CACHE = Path.home() / "Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Cache"


def _resolve_cache_path(path: str) -> str:
    if not path:
        return path
    if "{capcut_cache}" not in path:
        return path
    candidate = Path(path.replace("{capcut_cache}", str(CAPCUT_CACHE)))
    return str(candidate)


@dataclass(frozen=True)
class TransitionPreset:
    key: str
    name: str
    template: dict

    def formatted_template(self) -> dict:
        resolved = copy.deepcopy(self.template)
        raw_path = resolved.get("path")
        if raw_path:
            resolved["path"] = _resolve_cache_path(raw_path)
        return resolved


@dataclass(frozen=True)
class AnimationPreset:
    key: str
    name: str
    category: str  # in | out | combo
    template: dict
    default_duration_us: int

    def formatted_template(self) -> dict:
        resolved = copy.deepcopy(self.template)
        raw_path = resolved.get("path")
        if raw_path:
            resolved["path"] = _resolve_cache_path(raw_path)
        return resolved


@dataclass(frozen=True)
class EffectPreset:
    key: str
    name: str
    material_template: dict
    segment_template: dict

    def formatted_material(self) -> dict:
        resolved = copy.deepcopy(self.material_template)
        raw_path = resolved.get("path")
        if raw_path:
            resolved["path"] = _resolve_cache_path(raw_path)
        return resolved

    def formatted_segment(self) -> dict:
        return copy.deepcopy(self.segment_template)


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

FADE_IN = AnimationPreset(
    key="fade_in",
    name="Fade In",
    category="in",
    template={
        "anim_adjust_params": None,
        "category_id": "6824",
        "category_name": "In",
        "duration": 500000,
        "id": "6798320778182922760",
        "material_type": "video",
        "name": "Fade In",
        "panel": "video",
        "path": "{capcut_cache}/effect/6798320778182922760/883ad04bd79b502aaa55b5d9b87175ea",
        "platform": "all",
        "request_id": "",
        "resource_id": "6798320778182922760",
        "source_platform": 1,
        "start": 0,
        "third_resource_id": "6798320778182922760",
        "type": "in",
    },
    default_duration_us=500_000,
)

FADE_OUT = AnimationPreset(
    key="fade_out",
    name="Fade Out",
    category="out",
    template={
        "anim_adjust_params": None,
        "category_id": "6825",
        "category_name": "Out",
        "duration": 500000,
        "id": "6798320902548230669",
        "material_type": "video",
        "name": "Fade Out",
        "panel": "video",
        "path": "{capcut_cache}/effect/6798320902548230669/c6f05ce62355b537be762550040bfc08",
        "platform": "all",
        "request_id": "",
        "resource_id": "6798320902548230669",
        "source_platform": 1,
        "start": 0,
        "third_resource_id": "6798320902548230669",
        "type": "out",
    },
    default_duration_us=500_000,
)

BLACK_NOISE = EffectPreset(
    key="black_noise",
    name="Black Noise",
    material_template={
        "adjust_params": [
            {
                "default_value": 0.33,
                "name": "effects_adjust_speed",
                "value": 0.33,
            }
        ],
        "algorithm_artifact_path": "",
        "apply_target_type": 2,
        "apply_time_range": None,
        "bind_segment_id": "",
        "category_id": "1111",
        "category_name": "heycan_search_special_effect",
        "common_keyframes": [],
        "covering_relation_change": 0,
        "disable_effect_faces": [],
        "effect_id": "7399470796290166022",
        "effect_mask": [],
        "enable_mask": True,
        "formula_id": "",
        "id": "TEMPLATE",
        "item_effect_type": 0,
        "name": "Black Noise",
        "path": "{capcut_cache}/effect/7399470796290166022/e7baebcf969437d4d5cdb607578bbf89",
        "platform": "all",
        "render_index": 0,
        "request_id": "",
        "resource_id": "7399470796290166022",
        "source_platform": 1,
        "sub_type": 0,
        "time_range": None,
        "track_render_index": 0,
        "transparent_params": "",
        "type": "video_effect",
        "value": 1.0,
        "version": "",
    },
    segment_template={
        "caption_info": None,
        "cartoon": False,
        "clip": None,
        "color_correct_alg_result": "",
        "common_keyframes": [],
        "desc": "",
        "digital_human_template_group_id": "",
        "enable_adjust": False,
        "enable_adjust_mask": False,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_hsl": False,
        "enable_hsl_curves": True,
        "enable_lut": False,
        "enable_smart_color_adjust": False,
        "enable_video_mask": True,
        "extra_material_refs": [],
        "group_id": "",
        "hdr_settings": None,
        "id": "TEMPLATE",
        "intensifies_audio": False,
        "is_loop": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "lyric_keyframes": None,
        "material_id": "",
        "raw_segment_id": "",
        "render_index": 11000,
        "render_timerange": {"duration": 0, "start": 0},
        "responsive_layout": {
            "enable": False,
            "horizontal_pos_layout": 0,
            "size_layout": 0,
            "target_follow": "",
            "vertical_pos_layout": 0,
        },
        "reverse": False,
        "source": "segmentsourcenormal",
        "source_timerange": None,
        "speed": 1.0,
        "state": 0,
        "target_timerange": {"duration": 0, "start": 0},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 1,
        "uniform_scale": None,
        "visible": True,
        "volume": 1.0,
    },
)

TRANSITION_PRESETS: Dict[str, TransitionPreset] = {preset.key: preset for preset in (BLACK_FADE,)}
ANIMATION_PRESETS: Dict[str, AnimationPreset] = {preset.key: preset for preset in (FADE_IN, FADE_OUT)}
EFFECT_PRESETS: Dict[str, EffectPreset] = {preset.key: preset for preset in (BLACK_NOISE,)}
