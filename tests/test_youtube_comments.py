from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from autocapcut.services.youtube_comments import (
    DEFAULT_REPLY_SYSTEM_PROMPT,
    _build_openrouter_messages,
    _map_comment_item,
    _normalize_reply_text,
    VerifiedChannel,
    verify_openrouter_key,
)


class YouTubeCommentsServiceTests(unittest.TestCase):
    def test_map_comment_item_marks_owner_and_auto_reply_flags(self) -> None:
        raw = {
            "id": "thread-1",
            "snippet": {
                "videoId": "video-123",
                "totalReplyCount": 0,
                "topLevelComment": {
                    "id": "comment-1",
                    "snippet": {
                        "authorDisplayName": "Viewer A",
                        "authorChannelId": {"value": "viewer-channel"},
                        "textDisplay": "Great video",
                        "publishedAt": "2026-03-31T02:00:00Z",
                        "updatedAt": "2026-03-31T02:00:00Z",
                    },
                },
            },
        }

        item = _map_comment_item(raw, "owner-channel")

        self.assertEqual(item.comment_id, "comment-1")
        self.assertFalse(item.is_from_channel_owner)
        self.assertTrue(item.can_auto_reply)

    def test_map_comment_item_skips_owner_comment(self) -> None:
        raw = {
            "id": "thread-2",
            "snippet": {
                "totalReplyCount": 0,
                "topLevelComment": {
                    "id": "comment-2",
                    "snippet": {
                        "authorDisplayName": "Channel Owner",
                        "authorChannelId": {"value": "owner-channel"},
                        "textDisplay": "Pinned note",
                    },
                },
            },
        }

        item = _map_comment_item(raw, "owner-channel")

        self.assertTrue(item.is_from_channel_owner)
        self.assertFalse(item.can_auto_reply)

    def test_map_comment_item_includes_reply_thread(self) -> None:
        raw = {
            "id": "thread-3",
            "snippet": {
                "totalReplyCount": 1,
                "topLevelComment": {
                    "id": "comment-3",
                    "snippet": {
                        "authorDisplayName": "Viewer B",
                        "authorChannelId": {"value": "viewer-b"},
                        "textDisplay": "Thanks for this",
                    },
                },
            },
            "replies": {
                "comments": [
                    {
                        "id": "reply-1",
                        "snippet": {
                            "authorDisplayName": "SeniorForge 365",
                            "authorChannelId": {"value": "owner-channel"},
                            "textDisplay": "Thanks for watching.",
                            "publishedAt": "2026-04-07T10:00:00Z",
                        },
                    },
                ],
            },
        }

        item = _map_comment_item(raw, "owner-channel")

        self.assertEqual(item.reply_count, 1)
        self.assertEqual(len(item.replies), 1)
        self.assertEqual(item.replies[0].reply_id, "reply-1")
        self.assertTrue(item.replies[0].is_from_channel_owner)
        self.assertEqual(item.replies[0].text, "Thanks for watching.")

    def test_build_openrouter_messages_includes_brand_voice(self) -> None:
        messages = _build_openrouter_messages(
            comment_text="How often should I post?",
            channel_name="Dr Pickle",
            video_title="Menopause Tips",
            seeding_comments="Thank you so much!\nGlad it helped.\nStay tuned.",
            system_prompt="Reply warmly and thank the viewer.",
        )

        self.assertEqual(len(messages), 2)
        self.assertIn("Match this channel voice", messages[0]["content"])
        self.assertIn("Reply warmly and thank the viewer.", messages[0]["content"])
        self.assertIn("Dr Pickle", messages[1]["content"])
        self.assertIn("How often should I post?", messages[1]["content"])

    def test_build_openrouter_messages_uses_default_system_prompt_when_empty(self) -> None:
        messages = _build_openrouter_messages(
            comment_text="Great work",
            channel_name="SeniorForge 365",
            video_title="Healthy Aging Tips",
            seeding_comments="",
            system_prompt="",
        )

        self.assertIn(DEFAULT_REPLY_SYSTEM_PROMPT, messages[0]["content"])

    def test_normalize_reply_text_strips_code_fences_and_quotes(self) -> None:
        normalized = _normalize_reply_text('```text\n"Thanks a lot for watching!"\n```')
        self.assertEqual(normalized, "Thanks a lot for watching!")

    def test_verified_channel_dataclass_keeps_channel_identity(self) -> None:
        verified = VerifiedChannel(channel_id="UC123", channel_title="SeniorForge 365")
        self.assertEqual(verified.channel_id, "UC123")
        self.assertEqual(verified.channel_title, "SeniorForge 365")

    def test_verify_openrouter_key_accepts_successful_response(self) -> None:
        response = AsyncMock()
        response.status_code = 200
        response.text = "ok"

        client = AsyncMock()
        client.post.return_value = response

        async_client = AsyncMock()
        async_client.__aenter__.return_value = client
        async_client.__aexit__.return_value = None

        with patch("autocapcut.services.youtube_comments.httpx.AsyncClient", return_value=async_client):
            import asyncio
            asyncio.run(verify_openrouter_key(api_key="sk-or-v1-test"))
