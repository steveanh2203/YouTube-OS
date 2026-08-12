"""Validate AI33.pro API credentials without exposing them to the frontend."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import httpx


AI33_API_BASE_URL = str(os.getenv("AI33_API_BASE_URL", "https://api.ai33.pro")).strip().rstrip("/")
AI33_VERIFY_TIMEOUT = float(os.getenv("AI33_VERIFY_TIMEOUT", "15"))


@dataclass(slots=True)
class AI33VerificationResult:
    connected: bool
    message: str


def _response_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("message", "detail", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def verify_ai33_api_key(api_key: str) -> AI33VerificationResult:
    """Verify a key against AI33.pro's authenticated credits endpoint."""
    normalized_key = str(api_key or "").strip()
    if not normalized_key:
        return AI33VerificationResult(False, "AI33.pro API key is required.")

    try:
        async with httpx.AsyncClient(timeout=AI33_VERIFY_TIMEOUT) as client:
            response = await client.get(
                f"{AI33_API_BASE_URL}/v1/credits",
                headers={"xi-api-key": normalized_key},
            )
    except httpx.TimeoutException:
        return AI33VerificationResult(False, "AI33.pro did not respond in time.")
    except httpx.RequestError:
        return AI33VerificationResult(False, "Could not connect to AI33.pro.")

    try:
        payload: Any = response.json()
    except ValueError:
        payload = {}

    if response.status_code != 200:
        message = _response_message(payload)
        if response.status_code in (401, 403):
            return AI33VerificationResult(
                False,
                message or "AI33.pro rejected this API key.",
            )
        return AI33VerificationResult(
            False,
            message or f"AI33.pro verification failed ({response.status_code}).",
        )

    if isinstance(payload, dict) and payload.get("success") is False:
        return AI33VerificationResult(
            False,
            _response_message(payload) or "AI33.pro rejected this API key.",
        )

    return AI33VerificationResult(True, "AI33.pro API key connected successfully.")
