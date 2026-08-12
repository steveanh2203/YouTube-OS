from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, Mock, patch

import httpx

from autocapcut.api.routes import account_connect as account_connect_routes
from autocapcut.services import account_connect
from autocapcut.services.ai33_credentials import AI33VerificationResult, verify_ai33_api_key


def _client_context(response: Mock) -> AsyncMock:
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    return context


def test_verify_ai33_api_key_accepts_connected_key() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"success": True, "credits": 100}

    with patch(
        "autocapcut.services.ai33_credentials.httpx.AsyncClient",
        return_value=_client_context(response),
    ):
        result = asyncio.run(verify_ai33_api_key("valid-key"))

    assert result.connected is True
    assert result.message == "AI33.pro API key connected successfully."


def test_verify_ai33_api_key_rejects_unauthorized_key() -> None:
    response = Mock(status_code=401)
    response.json.return_value = {"success": False, "message": "Unauthorized"}

    with patch(
        "autocapcut.services.ai33_credentials.httpx.AsyncClient",
        return_value=_client_context(response),
    ):
        result = asyncio.run(verify_ai33_api_key("invalid-key"))

    assert result.connected is False
    assert result.message == "Unauthorized"


def test_verify_ai33_api_key_handles_connection_error() -> None:
    client = AsyncMock()
    client.get.side_effect = httpx.ConnectError("offline")
    context = AsyncMock()
    context.__aenter__.return_value = client

    with patch(
        "autocapcut.services.ai33_credentials.httpx.AsyncClient",
        return_value=context,
    ):
        result = asyncio.run(verify_ai33_api_key("valid-key"))

    assert result.connected is False
    assert result.message == "Could not connect to AI33.pro."


def test_verify_ai33_route_saves_key_without_returning_it() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / "account-connect.json"
        with patch.dict(
            os.environ,
            {account_connect.ACCOUNT_CONNECT_FILE_ENV: str(state_path)},
        ), patch(
            "autocapcut.api.routes.account_connect.verify_ai33_api_key",
            new=AsyncMock(return_value=AI33VerificationResult(True, "Connected.")),
        ):
            response = asyncio.run(account_connect_routes.verify_ai33_settings(
                account_connect_routes.AI33SettingsVerifyRequest(api_key="secret-ai33-key"),
            ))

            assert account_connect.get_ai33_settings()["api_key"] == "secret-ai33-key"
            assert response.settings.connected is True
            assert "secret-ai33-key" not in response.model_dump_json()
