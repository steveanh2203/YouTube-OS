from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from autocapcut.database.models import CommunityPost
from autocapcut.services.community_posting import (
    CommunityPostingError,
    normalize_channel_url,
    queue_post,
    retry_post,
    should_publish_now,
)


class CommunityPostingServiceTests(unittest.TestCase):
    def test_queue_post_accepts_opaque_workspace_image_reference(self) -> None:
        from autocapcut.services.media_workspace import (
            create_workspace_directory,
            resolve_workspace_reference,
            workspace_file_reference,
        )

        with TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir}):
            directory = create_workspace_directory("Community")
            image = resolve_workspace_reference(directory.reference) / "post.jpg"
            image.write_bytes(b"image")
            reference = workspace_file_reference(directory.reference, image)
            post = CommunityPost(
                parent_project_id=1,
                body="Post",
                channel_url="https://youtube.com/@channel",
                image_path=reference,
            )

            queued = queue_post(post)

            self.assertEqual(queued.status, "queued")
            self.assertEqual(queued.image_path, reference)

    def test_media_image_reference_resolves_inside_managed_workspace(self) -> None:
        from autocapcut.services import community_posting

        with TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir}):
            image = Path(temp_dir) / "media" / "asset-1" / "post.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"image")
            database = Path(temp_dir) / "community.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE media_assets (id TEXT PRIMARY KEY, stored_path TEXT NOT NULL)")
                connection.execute(
                    "INSERT INTO media_assets (id, stored_path) VALUES (?, ?)",
                    ("asset-1", str(image)),
                )
                connection.commit()
            finally:
                connection.close()

            with patch.object(community_posting, "DB_PATH", database):
                resolved = community_posting.resolve_community_image_path("media:asset-1")

            self.assertEqual(resolved, image.resolve())

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

