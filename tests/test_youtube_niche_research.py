from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, Mock, patch

from autocapcut.api.routes import niche_research as niche_routes
from autocapcut.services import account_connect
from autocapcut.services.youtube_niche_research import (
    YouTubeNicheResearchError,
    _keyword_ideas,
    _summary,
    verify_youtube_api_key,
)


def _client_context(response: Mock) -> AsyncMock:
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    return context


def test_verify_youtube_api_key_accepts_connected_key() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"items": [{"id": "video-1"}]}

    with patch(
        "autocapcut.services.youtube_niche_research.httpx.AsyncClient",
        return_value=_client_context(response),
    ):
        asyncio.run(verify_youtube_api_key("valid-key"))


def test_verify_youtube_api_key_rejects_api_error() -> None:
    response = Mock(status_code=403)
    response.json.return_value = {"error": {"message": "API key not valid."}}

    with patch(
        "autocapcut.services.youtube_niche_research.httpx.AsyncClient",
        return_value=_client_context(response),
    ):
        try:
            asyncio.run(verify_youtube_api_key("invalid-key"))
        except YouTubeNicheResearchError as exc:
            assert str(exc) == "API key not valid."
        else:
            raise AssertionError("Invalid YouTube API key should be rejected.")


def test_keyword_ideas_extract_repeated_title_phrases() -> None:
    ideas = _keyword_ideas([
        {"title": "Easy sleep stories for adults", "views": 40_000, "views_per_day": 500},
        {"title": "Calm sleep stories for deep rest", "views": 60_000, "views_per_day": 700},
        {"title": "Sleep stories with rain", "views": 20_000, "views_per_day": 250},
    ], "sleep")

    by_keyword = {item["keyword"]: item for item in ideas}
    assert by_keyword["stories"]["occurrences"] == 3
    assert by_keyword["sleep stories"]["occurrences"] == 3


def test_summary_returns_bounded_transparent_scores() -> None:
    summary = _summary([
        {
            "views": 90_000,
            "views_per_day": 900,
            "channel_subscribers": 20_000,
            "subscriber_count_hidden": False,
        },
        {
            "views": 45_000,
            "views_per_day": 450,
            "channel_subscribers": 250_000,
            "subscriber_count_hidden": False,
        },
        {
            "views": 10_000,
            "views_per_day": 100,
            "channel_subscribers": 5_000,
            "subscriber_count_hidden": False,
        },
    ])

    assert summary["result_count"] == 3
    assert summary["median_views"] == 45_000
    assert summary["median_views_per_day"] == 450
    assert 0 <= summary["opportunity_score"] <= 100
    assert 0 <= summary["competition_score"] <= 100
    assert summary["small_channel_breakout_rate"] > 0


def test_verify_route_saves_key_without_returning_it() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / "account-connect.json"
        with patch.dict(
            os.environ,
            {account_connect.ACCOUNT_CONNECT_FILE_ENV: str(state_path)},
        ), patch(
            "autocapcut.api.routes.niche_research.verify_youtube_api_key",
            new=AsyncMock(return_value=None),
        ):
            response = asyncio.run(niche_routes.verify_settings(
                niche_routes.YouTubeDataKeyVerifyRequest(api_key="secret-youtube-key"),
            ))

            assert account_connect.get_youtube_data_settings()["api_key"] == "secret-youtube-key"
            assert response.settings.connected is True
            assert "secret-youtube-key" not in response.model_dump_json()
