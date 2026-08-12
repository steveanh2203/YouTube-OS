"""YouTube Data API client and transparent niche-opportunity heuristics."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import html
import math
import os
import re
from statistics import median
from typing import Any

import httpx


YOUTUBE_API_BASE_URL = str(
    os.getenv("YOUTUBE_DATA_API_BASE_URL", "https://www.googleapis.com/youtube/v3")
).strip().rstrip("/")
YOUTUBE_API_TIMEOUT = float(os.getenv("YOUTUBE_DATA_API_TIMEOUT", "20"))

_STOP_WORDS = {
    "about", "after", "again", "against", "also", "and", "are", "best", "but", "can",
    "cho", "cua", "của", "day", "đây", "for", "from", "guide", "how", "into", "la", "là",
    "moi", "mới", "nhung", "những", "not", "review", "the", "this", "that", "tips", "top",
    "tren", "trên", "tutorial", "video", "voi", "với", "what", "when", "where", "why", "you",
    "your", "2024", "2025", "2026",
}


class YouTubeNicheResearchError(RuntimeError):
    """Safe error raised for YouTube API and analysis failures."""


def _error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    message = payload.get("message")
    return message.strip() if isinstance(message, str) else ""


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = await client.get(f"{YOUTUBE_API_BASE_URL}/{path}", params=params)
    except httpx.TimeoutException as exc:
        raise YouTubeNicheResearchError("YouTube Data API did not respond in time.") from exc
    except httpx.RequestError as exc:
        raise YouTubeNicheResearchError("Could not connect to YouTube Data API.") from exc

    try:
        payload: Any = response.json()
    except ValueError:
        payload = {}

    if response.status_code != 200:
        message = _error_message(payload)
        if response.status_code in (400, 401, 403):
            raise YouTubeNicheResearchError(message or "YouTube rejected this API key or request.")
        raise YouTubeNicheResearchError(message or f"YouTube Data API failed ({response.status_code}).")
    if not isinstance(payload, dict):
        raise YouTubeNicheResearchError("YouTube Data API returned an invalid response.")
    return payload


async def verify_youtube_api_key(api_key: str) -> None:
    normalized_key = str(api_key or "").strip()
    if not normalized_key:
        raise YouTubeNicheResearchError("YouTube Data API key is required.")

    async with httpx.AsyncClient(timeout=YOUTUBE_API_TIMEOUT) as client:
        await _get_json(client, "videos", {
            "part": "id",
            "chart": "mostPopular",
            "maxResults": 1,
            "regionCode": "US",
            "key": normalized_key,
        })


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _published_datetime(value: Any) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _tokenize(title: str) -> list[str]:
    normalized = html.unescape(title).casefold().replace("_", " ")
    return [
        token
        for token in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
        if len(token) >= 3 and token not in _STOP_WORDS and not token.isdigit()
    ]


def _keyword_ideas(videos: list[dict[str, Any]], seed_query: str) -> list[dict[str, Any]]:
    occurrences: Counter[str] = Counter()
    views_by_keyword: defaultdict[str, list[int]] = defaultdict(list)
    velocity_by_keyword: defaultdict[str, list[float]] = defaultdict(list)

    for video in videos:
        tokens = _tokenize(str(video.get("title") or ""))
        phrases = set(tokens)
        phrases.update(" ".join(tokens[index:index + 2]) for index in range(len(tokens) - 1))
        for phrase in phrases:
            occurrences[phrase] += 1
            views_by_keyword[phrase].append(_as_int(video.get("views")))
            velocity_by_keyword[phrase].append(float(video.get("views_per_day") or 0.0))

    seed = seed_query.casefold().strip()
    ranked: list[dict[str, Any]] = []
    for keyword, count in occurrences.items():
        if keyword == seed or count < 2:
            continue
        keyword_views = views_by_keyword[keyword]
        keyword_velocity = velocity_by_keyword[keyword]
        avg_views = round(sum(keyword_views) / len(keyword_views)) if keyword_views else 0
        avg_velocity = round(sum(keyword_velocity) / len(keyword_velocity), 1) if keyword_velocity else 0.0
        rank_score = count * 15 + math.log10(avg_views + 1) * 8 + math.log10(avg_velocity + 1) * 5
        ranked.append({
            "keyword": keyword,
            "occurrences": count,
            "avg_views": avg_views,
            "avg_views_per_day": avg_velocity,
            "score": round(rank_score, 1),
        })

    ranked.sort(key=lambda item: (item["score"], item["occurrences"]), reverse=True)
    return ranked[:12]


def _summary(videos: list[dict[str, Any]]) -> dict[str, Any]:
    if not videos:
        return {
            "opportunity_score": 0,
            "demand_score": 0,
            "competition_score": 0,
            "median_views": 0,
            "median_views_per_day": 0.0,
            "median_subscribers": 0,
            "small_channel_breakout_rate": 0,
            "result_count": 0,
        }

    views = [_as_int(video.get("views")) for video in videos]
    velocities = [float(video.get("views_per_day") or 0.0) for video in videos]
    subscribers = [
        _as_int(video.get("channel_subscribers"))
        for video in videos
        if not bool(video.get("subscriber_count_hidden"))
    ]
    median_views = round(median(views))
    median_velocity = round(median(velocities), 1)
    median_subscribers = round(median(subscribers)) if subscribers else 0

    demand_score = min(100, round(math.log10(median_views + 1) / 6 * 100))
    subscriber_competition = min(100, round(math.log10(median_subscribers + 1) / 7 * 100))
    total_views = sum(views)
    top_share = (sum(sorted(views, reverse=True)[:3]) / total_views * 100) if total_views else 0
    competition_score = min(100, round(subscriber_competition * 0.65 + top_share * 0.35))

    eligible_breakouts = 0
    breakout_count = 0
    for video in videos:
        if bool(video.get("subscriber_count_hidden")):
            continue
        channel_subscribers = _as_int(video.get("channel_subscribers"))
        eligible_breakouts += 1
        if channel_subscribers <= 100_000 and _as_int(video.get("views")) >= max(10_000, channel_subscribers * 1.5):
            breakout_count += 1
    breakout_rate = round((breakout_count / eligible_breakouts * 100), 1) if eligible_breakouts else 0.0

    velocity_score = min(100, round(math.log10(median_velocity + 1) / 4 * 100))
    opportunity_score = min(100, round(
        demand_score * 0.40
        + (100 - competition_score) * 0.25
        + breakout_rate * 0.20
        + velocity_score * 0.15
    ))

    return {
        "opportunity_score": opportunity_score,
        "demand_score": demand_score,
        "competition_score": competition_score,
        "median_views": median_views,
        "median_views_per_day": median_velocity,
        "median_subscribers": median_subscribers,
        "small_channel_breakout_rate": breakout_rate,
        "result_count": len(videos),
    }


async def research_youtube_niche(
    *,
    api_key: str,
    query: str,
    region_code: str = "US",
    relevance_language: str = "en",
    published_within_days: int = 365,
    video_duration: str = "any",
    max_results: int = 25,
) -> dict[str, Any]:
    normalized_key = str(api_key or "").strip()
    normalized_query = str(query or "").strip()
    if not normalized_key:
        raise YouTubeNicheResearchError("Configure a YouTube Data API key in Settings first.")
    if len(normalized_query) < 2:
        raise YouTubeNicheResearchError("Enter a niche or seed keyword with at least 2 characters.")

    published_after = datetime.now(timezone.utc) - timedelta(days=max(7, min(published_within_days, 3650)))
    search_params: dict[str, Any] = {
        "part": "snippet",
        "type": "video",
        "q": normalized_query,
        "maxResults": max(5, min(max_results, 50)),
        "order": "relevance",
        "regionCode": region_code.upper(),
        "relevanceLanguage": relevance_language,
        "publishedAfter": published_after.isoformat().replace("+00:00", "Z"),
        "safeSearch": "moderate",
        "key": normalized_key,
    }
    if video_duration in {"short", "medium", "long"}:
        search_params["videoDuration"] = video_duration

    async with httpx.AsyncClient(timeout=YOUTUBE_API_TIMEOUT) as client:
        search_payload = await _get_json(client, "search", search_params)
        search_items = search_payload.get("items") if isinstance(search_payload.get("items"), list) else []
        video_ids = [
            str(item.get("id", {}).get("videoId") or "").strip()
            for item in search_items
            if isinstance(item, dict) and isinstance(item.get("id"), dict)
        ]
        video_ids = [video_id for video_id in video_ids if video_id]
        if not video_ids:
            return {
                "ok": True,
                "query": normalized_query,
                "summary": _summary([]),
                "keyword_ideas": [],
                "videos": [],
                "methodology": "Directional proxy based on public YouTube results; not search volume.",
            }

        video_payload = await _get_json(client, "videos", {
            "part": "snippet,statistics",
            "id": ",".join(video_ids),
            "key": normalized_key,
        })
        raw_videos = video_payload.get("items") if isinstance(video_payload.get("items"), list) else []
        channel_ids = list(dict.fromkeys(
            str(item.get("snippet", {}).get("channelId") or "").strip()
            for item in raw_videos
            if isinstance(item, dict) and isinstance(item.get("snippet"), dict)
        ))
        channel_ids = [channel_id for channel_id in channel_ids if channel_id]

        channel_by_id: dict[str, dict[str, Any]] = {}
        if channel_ids:
            channel_payload = await _get_json(client, "channels", {
                "part": "statistics",
                "id": ",".join(channel_ids),
                "key": normalized_key,
            })
            for item in channel_payload.get("items") or []:
                if isinstance(item, dict):
                    channel_by_id[str(item.get("id") or "")] = item

    now = datetime.now(timezone.utc)
    videos: list[dict[str, Any]] = []
    for item in raw_videos:
        if not isinstance(item, dict):
            continue
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        channel_id = str(snippet.get("channelId") or "")
        channel_stats = channel_by_id.get(channel_id, {}).get("statistics") or {}
        published_at = _published_datetime(snippet.get("publishedAt"))
        age_days = max(1.0, (now - published_at).total_seconds() / 86_400)
        views = _as_int(stats.get("viewCount"))
        thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        thumbnail = thumbnails.get("medium") or thumbnails.get("default") or {}
        videos.append({
            "video_id": str(item.get("id") or ""),
            "title": html.unescape(str(snippet.get("title") or "")),
            "channel_id": channel_id,
            "channel_title": html.unescape(str(snippet.get("channelTitle") or "")),
            "published_at": published_at.isoformat(),
            "thumbnail_url": str(thumbnail.get("url") or "") if isinstance(thumbnail, dict) else "",
            "views": views,
            "likes": _as_int(stats.get("likeCount")),
            "comments": _as_int(stats.get("commentCount")),
            "views_per_day": round(views / age_days, 1),
            "channel_subscribers": _as_int(channel_stats.get("subscriberCount")),
            "subscriber_count_hidden": bool(channel_stats.get("hiddenSubscriberCount")),
        })

    videos.sort(key=lambda item: item["views_per_day"], reverse=True)
    return {
        "ok": True,
        "query": normalized_query,
        "summary": _summary(videos),
        "keyword_ideas": _keyword_ideas(videos, normalized_query),
        "videos": videos,
        "methodology": "Directional proxy based on public YouTube results; not search volume.",
    }
