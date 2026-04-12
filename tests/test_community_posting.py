from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from autocapcut.database.models import CommunityPost
from autocapcut.services.community_posting import (
    CommunityPostingError,
    normalize_channel_url,
    queue_post,
    retry_post,
    should_publish_now,
)


class CommunityPostingServiceTests(unittest.TestCase):
    def test_normalize_channel_url_adds_scheme_and_trims_slash(self) -> None:
        normalized = normalize_channel_url("youtube.com/@seniorforge365/")  # type: ignore[arg-type]
        self.assertEqual(normalized, "https://youtube.com/@seniorforge365")

    def test_queue_post_requires_body_and_channel_url(self) -> None:
        post = CommunityPost(parent_project_id=1, body="", channel_url=None)
        with self.assertRaisesRegex(CommunityPostingError, "Post body is required"):
            queue_post(post)

    def test_queue_post_requires_schedule_time_for_scheduled_posts(self) -> None:
        post = CommunityPost(
            parent_project_id=1,
            body="Draft",
            channel_url="https://youtube.com/@channel",
            post_mode="schedule",
            schedule_at=None,
        )
        with self.assertRaisesRegex(CommunityPostingError, "Schedule time is required"):
            queue_post(post)

    def test_retry_post_blocks_force_less_retry_after_success_marker(self) -> None:
        post = CommunityPost(
            parent_project_id=1,
            body="Draft",
            channel_url="https://youtube.com/@channel",
            status="failed",
            youtube_post_url="https://youtube.com/post/abc123",
        )
        with self.assertRaisesRegex(CommunityPostingError, "already has a YouTube result"):
            retry_post(post, force=False)

    def test_should_publish_now_handles_immediate_and_scheduled_posts(self) -> None:
        now = datetime.utcnow()
        immediate = CommunityPost(parent_project_id=1, body="Now", channel_url="https://youtube.com/@channel", status="queued", post_mode="now")
        future = CommunityPost(
            parent_project_id=1,
            body="Later",
            channel_url="https://youtube.com/@channel",
            status="queued",
            post_mode="schedule",
            schedule_at=now + timedelta(minutes=5),
        )

        self.assertTrue(should_publish_now(immediate, now))
        self.assertFalse(should_publish_now(future, now))

