"""Animation / Transition / Effect routes."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from autocapcut.models import ProjectItem, ProjectSource, ProjectStatus
from autocapcut.services import animation as anim_svc
from autocapcut.services import animation_presets as presets
from autocapcut.services.animation import AnimationError, TransitionError

router = APIRouter()


# ─── Preset catalogue ─────────────────────────────────────────────────────────

class PresetInfo(BaseModel):
    key: str
    name: str
    category: str   # transition | in | out | combo | effect


@router.get("/presets", response_model=List[PresetInfo])
async def list_presets() -> List[PresetInfo]:
    result: List[PresetInfo] = []

    for attr in dir(presets):
        obj = getattr(presets, attr)
        if isinstance(obj, presets.TransitionPreset):
            result.append(PresetInfo(key=obj.key, name=obj.name, category="transition"))
        elif isinstance(obj, presets.AnimationPreset):
            result.append(PresetInfo(key=obj.key, name=obj.name, category=obj.category))
        elif isinstance(obj, presets.EffectPreset):
            result.append(PresetInfo(key=obj.key, name=obj.name, category="effect"))

    return result


# ─── Apply ────────────────────────────────────────────────────────────────────

class AnimationRequest(BaseModel):
    project_path: str
    action: Literal["transition", "animation_in", "animation_out", "animation_combo", "effect", "clear_transitions", "clear_animations", "clear_effects"]
    preset_key: str | None = None


class AnimationResponse(BaseModel):
    ok: bool
    message: str


def _make_item(path: str) -> ProjectItem:
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {path}")
    return ProjectItem(name=p.name, path=str(p), source=ProjectSource.local, status=ProjectStatus.pending)


def _find_preset(key: str) -> presets.TransitionPreset | presets.AnimationPreset | presets.EffectPreset | None:
    for attr in dir(presets):
        obj = getattr(presets, attr)
        if isinstance(obj, (presets.TransitionPreset, presets.AnimationPreset, presets.EffectPreset)):
            if obj.key == key:
                return obj
    return None


@router.post("/apply", response_model=AnimationResponse)
async def apply_animation(req: AnimationRequest) -> AnimationResponse:
    item = _make_item(req.project_path)
    loop = asyncio.get_event_loop()

    try:
        if req.action == "clear_transitions":
            count = await loop.run_in_executor(None, lambda: anim_svc.clear_transitions(item))
            return AnimationResponse(ok=True, message=f"Cleared {count} transitions")

        if req.action == "clear_animations":
            summary = await loop.run_in_executor(None, lambda: anim_svc.remove_animations(item))
            return AnimationResponse(ok=True, message=f"Removed {summary.removed_entries} animation entries from {summary.affected_segments} segment(s)")

        if req.action == "clear_effects":
            summary = await loop.run_in_executor(None, lambda: anim_svc.remove_effects(item))
            return AnimationResponse(ok=True, message=f"Removed {summary.removed_entries} effect entries, {summary.removed_segments} segment(s)")

        # Preset-based actions
        if not req.preset_key:
            raise HTTPException(status_code=422, detail="preset_key required for this action")

        preset = _find_preset(req.preset_key)
        if preset is None:
            raise HTTPException(status_code=404, detail=f"Preset not found: {req.preset_key}")

        if req.action == "transition":
            if not isinstance(preset, presets.TransitionPreset):
                raise HTTPException(status_code=422, detail="Preset is not a transition")
            _preset_key = req.preset_key
            _duration = 0.5
            summary = await loop.run_in_executor(
                None,
                lambda: anim_svc.apply_transition(item, preset_key=_preset_key, duration_seconds=_duration),
            )
            return AnimationResponse(ok=True, message=f"Applied transition to {summary.applied_transitions} segments")

        if req.action in ("animation_in", "animation_out", "animation_combo"):
            if not isinstance(preset, presets.AnimationPreset):
                raise HTTPException(status_code=422, detail="Preset is not an animation")
            _preset_key = req.preset_key
            summary = await loop.run_in_executor(
                None,
                lambda: anim_svc.apply_animations(item, preset_keys=[_preset_key], duration_seconds=0.0),
            )
            return AnimationResponse(ok=True, message=f"Applied animation to {summary.segments_updated} segment(s)")

        if req.action == "effect":
            if not isinstance(preset, presets.EffectPreset):
                raise HTTPException(status_code=422, detail="Preset is not an effect")
            _preset_key = req.preset_key
            summary = await loop.run_in_executor(
                None,
                lambda: anim_svc.apply_effect(item, preset_key=_preset_key),
            )
            return AnimationResponse(ok=True, message=f"Applied effect to {summary.segments_added} segment(s)")

        raise HTTPException(status_code=422, detail=f"Unknown action: {req.action}")

    except (AnimationError, TransitionError) as exc:
        return AnimationResponse(ok=False, message=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        return AnimationResponse(ok=False, message=f"Unexpected: {exc}")

