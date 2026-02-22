"""Load active CapCut shortcuts from the local User Data config."""
from __future__ import annotations

import configparser
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShortcutProfile:
    """Resolved active keymap profile and its shortcut sequence."""

    name: str | None
    source: Path | None
    sequence: dict[str, list[str]]


def load_active_shortcut_profile(user_data_dir: Path) -> ShortcutProfile:
    """Return the currently active CapCut shortcut profile from disk."""

    config_dir = user_data_dir / "Config"
    shortcut_dir = config_dir / "Shortcut"
    combined_path = shortcut_dir / "combined.json"
    settings_path = config_dir / "keymapSettings"

    current_index, current_name = _read_keymap_settings(settings_path)

    combined_data: dict[str, dict] = {}
    if combined_path.is_file():
        try:
            parsed = json.loads(combined_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                combined_data = parsed
        except Exception:
            combined_data = {}

    if combined_data:
        profile_name = _pick_profile_name(combined_data, current_index, current_name)
        if profile_name:
            profile_obj = combined_data.get(profile_name, {})
            sequence = profile_obj.get("sequence", {})
            if isinstance(sequence, dict):
                cleaned: dict[str, list[str]] = {}
                for key, value in sequence.items():
                    if isinstance(value, list):
                        cleaned[key] = [str(item) for item in value]
                return ShortcutProfile(profile_name, combined_path, cleaned)

    # Fallback to individual profile files.
    fallback_names = ["Custom1", "Custom2", "Custom3", "Final Cut Pro X", "Premiere Pro"]
    if current_name:
        fallback_names.insert(0, current_name)
    if current_index is not None and 0 <= current_index < len(fallback_names):
        fallback_names.insert(0, fallback_names[current_index])

    seen: set[str] = set()
    for name in fallback_names:
        if name in seen:
            continue
        seen.add(name)
        profile_path = shortcut_dir / f"{name}.json"
        if not profile_path.is_file():
            continue
        try:
            parsed = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sequence = parsed.get("sequence", {}) if isinstance(parsed, dict) else {}
        if not isinstance(sequence, dict):
            continue
        cleaned: dict[str, list[str]] = {}
        for key, value in sequence.items():
            if isinstance(value, list):
                cleaned[key] = [str(item) for item in value]
        if cleaned:
            return ShortcutProfile(name, profile_path, cleaned)

    return ShortcutProfile(None, None, {})


def resolve_action_shortcut(
    profile: ShortcutProfile,
    action_name: str,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    """Convert a CapCut action shortcut to pyautogui key names."""

    values = profile.sequence.get(action_name) or []
    for value in values:
        parsed = parse_capcut_shortcut(value)
        if parsed:
            return parsed
    return fallback


def parse_capcut_shortcut(raw_shortcut: str) -> tuple[str, ...] | None:
    """Parse a single CapCut shortcut string into pyautogui keys."""

    combo = raw_shortcut.strip().replace("＋", "+")
    if not combo:
        return None
    if "mousebutton" in combo.casefold():
        return None
    if combo.casefold().endswith("_hold"):
        return None

    parts = [part.strip() for part in combo.split("+") if part.strip()]
    if not parts:
        return None

    modifiers: list[str] = []
    keys: list[str] = []
    for part in parts:
        normalized = part.casefold()

        # CapCut uses "Ctrl" in keymap labels, map to mac Command key.
        if normalized in {"ctrl", "control", "cmd", "command", "meta", "super"}:
            modifiers.append("command")
            continue
        if normalized in {"alt", "option"}:
            modifiers.append("alt")
            continue
        if normalized == "shift":
            modifiers.append("shift")
            continue
        if normalized in {"fn", "function"}:
            modifiers.append("fn")
            continue

        key = _map_key_token(normalized)
        if key is None:
            return None
        keys.append(key)

    if not keys:
        return None
    return tuple(modifiers + keys)


def _read_keymap_settings(path: Path) -> tuple[int | None, str | None]:
    if not path.is_file():
        return None, None

    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except Exception:
        return None, None

    section = parser["General"] if parser.has_section("General") else parser.defaults()
    raw_index = section.get("currentKeymapIndex")
    raw_name = section.get("currentKeymapName")

    index: int | None = None
    if raw_index:
        try:
            index = int(raw_index)
        except ValueError:
            index = None
    name = raw_name.strip() if raw_name else None
    return index, name


def _pick_profile_name(
    profiles: dict[str, dict],
    current_index: int | None,
    current_name: str | None,
) -> str | None:
    if current_name and current_name in profiles:
        return current_name

    names = list(profiles.keys())
    if current_index is not None and 0 <= current_index < len(names):
        return names[current_index]

    return names[0] if names else None


def _map_key_token(token: str) -> str | None:
    named = {
        "esc": "escape",
        "escape": "escape",
        "return": "enter",
        "enter": "enter",
        "space": "space",
        "tab": "tab",
        "home": "home",
        "end": "end",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "backspace": "backspace",
        "del": "delete",
        "delete": "delete",
    }
    if token in named:
        return named[token]

    if len(token) == 1:
        return token.lower()

    if token.startswith("f") and token[1:].isdigit():
        return token.lower()

    return None
