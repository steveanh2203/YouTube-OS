from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://api.minimaxi.com"
FALLBACK_BASE_URL = "https://api.minimax.io"
DEFAULT_TIMEOUT = 45.0


class MiniMaxAudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class MiniMaxVoice:
    voice_id: str
    voice_name: str
    voice_type: str
    created_time: str | None = None


def _base_url() -> str:
    raw = str(os.getenv("AUTOCAPCUT_MINIMAX_BASE_URL", DEFAULT_BASE_URL)).strip().rstrip("/")
    return raw or DEFAULT_BASE_URL


def _base_urls() -> list[str]:
    primary = _base_url()
    urls = [primary]
    if primary != FALLBACK_BASE_URL:
        urls.append(FALLBACK_BASE_URL)
    return urls


def _resolve_api_key(override: str | None = None) -> str:
    key = (override or os.getenv("AUTOCAPCUT_MINIMAX_API_KEY", "")).strip()
    if not key:
        raise MiniMaxAudioError("MiniMax API key is missing.")
    return key


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def list_voices(*, api_key_override: str | None = None, voice_type: str = "all") -> list[MiniMaxVoice]:
    api_key = _resolve_api_key(api_key_override)
    payload = {"voice_type": voice_type}

    res = None
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        for base_url in _base_urls():
            try:
                res = await client.post(f"{base_url}/v1/get_voice", headers=_headers(api_key), json=payload)
                break
            except httpx.HTTPError:
                continue

    if res is None:
        raise MiniMaxAudioError("MiniMax voice list request failed.")

    if res.status_code >= 400:
        raise MiniMaxAudioError(_extract_error(res))

    data = res.json()
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (0, None):
        raise MiniMaxAudioError(base_resp.get("status_msg") or "MiniMax voice list failed.")

    voices: list[MiniMaxVoice] = []
    for section in ("system_voice", "voice_cloning", "voice_generation"):
        for item in data.get(section) or []:
            voice_id = str(item.get("voice_id") or "").strip()
            if not voice_id:
                continue
            voices.append(
                MiniMaxVoice(
                    voice_id=voice_id,
                    voice_name=str(item.get("voice_name") or voice_id).strip(),
                    voice_type=section,
                    created_time=str(item.get("created_time") or "").strip() or None,
                )
            )

    return voices


async def synthesize_text(
    *,
    text: str,
    voice_id: str,
    model: str,
    speed: float = 1.0,
    volume: float = 1.0,
    pitch: int = 0,
    emotion: str | None = None,
    api_key_override: str | None = None,
) -> bytes:
    api_key = _resolve_api_key(api_key_override)
    clean_text = text.strip()
    if not clean_text:
        raise MiniMaxAudioError("Text is empty.")
    if len(clean_text) > 10000:
        raise MiniMaxAudioError("Text is too long for MiniMax sync TTS.")

    payload: dict[str, Any] = {
        "model": model,
        "text": clean_text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": volume,
            "pitch": pitch,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
        "output_format": "hex",
        "subtitle_enable": False,
    }
    if emotion:
        payload["voice_setting"]["emotion"] = emotion

    res = None
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        for base_url in _base_urls():
            try:
                res = await client.post(f"{base_url}/v1/t2a_v2", headers=_headers(api_key), json=payload)
                break
            except httpx.HTTPError:
                continue

    if res is None:
        raise MiniMaxAudioError("MiniMax TTS request failed.")

    if res.status_code >= 400:
        raise MiniMaxAudioError(_extract_error(res))

    data = res.json()
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (0, None):
        raise MiniMaxAudioError(base_resp.get("status_msg") or "MiniMax TTS failed.")

    audio_hex = str(((data.get("data") or {}).get("audio")) or "").strip()
    if not audio_hex:
        raise MiniMaxAudioError("MiniMax returned empty audio.")

    try:
        return bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise MiniMaxAudioError("MiniMax returned invalid audio payload.") from exc


def minimax_configured(*, api_key_override: str | None = None) -> bool:
    try:
        return bool(_resolve_api_key(api_key_override))
    except MiniMaxAudioError:
        return False


def _extract_error(res: httpx.Response) -> str:
    try:
        payload = res.json()
    except Exception:
        return f"MiniMax request failed ({res.status_code})."

    base_resp = payload.get("base_resp") or {}
    status_msg = str(base_resp.get("status_msg") or "").strip()
    if status_msg:
        return status_msg

    message = payload.get("message") or payload.get("detail")
    if message:
        return str(message)

    return f"MiniMax request failed ({res.status_code})."
