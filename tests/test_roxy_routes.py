from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autocapcut.api.routes import roxy
from autocapcut.services.roxy_upload import RoxyUploadSummary


class RoxyRouteTests(unittest.TestCase):
    def test_workspace_folder_videos_return_opaque_file_references(self) -> None:
        from autocapcut.services.media_workspace import create_workspace_directory, resolve_workspace_reference

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ", {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir}
        ):
            directory = create_workspace_directory("Roxy batch")
            folder = resolve_workspace_reference(directory.reference)
            (folder / "clip.mp4").write_bytes(b"video")

            result = roxy.asyncio.run(
                roxy.list_folder_videos(roxy.RoxyFolderVideosRequest(folder_path=directory.reference))
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.folder_path, directory.reference)
            self.assertEqual(len(result.videos), 1)
            self.assertTrue(result.videos[0].startswith("workspace-file:"))
            self.assertNotIn(str(folder), result.videos[0])

    def test_list_folder_videos_returns_sorted_supported_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "clip_b.mov").write_bytes(b"b")
            (folder / "clip_a.mp4").write_bytes(b"a")
            (folder / "notes.txt").write_text("ignore", encoding="utf-8")

            result = roxy.asyncio.run(
                roxy.list_folder_videos(roxy.RoxyFolderVideosRequest(folder_path=str(folder)))
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.videos,
            [str(folder / "clip_a.mp4"), str(folder / "clip_b.mov")],
        )

    def test_list_folder_videos_rejects_missing_folder(self) -> None:
        result = roxy.asyncio.run(
            roxy.list_folder_videos(roxy.RoxyFolderVideosRequest(folder_path="/tmp/does-not-exist-roxy"))
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.videos, [])

    def test_start_upload_passes_schedule_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "clip.mp4"
            video.write_bytes(b"video")

            with patch.object(roxy, "upload_video_via_roxy") as mock_upload:
                mock_upload.return_value = RoxyUploadSummary(
                    profile_id="profile-1",
                    video_path=video,
                    debugger_address="127.0.0.1:9222",
                    message="ok",
                    schedule_applied=True,
                    scheduled_at="2026-04-08T20:30:00",
                )
                result = roxy.asyncio.run(
                    roxy.start_upload(
                        roxy.RoxyUploadRequest(
                            api_host="http://127.0.0.1:50000",
                            api_token="token",
                            workspace_id=1,
                            profile_id="profile-1",
                            video_path=str(video),
                            schedule_at="2026-04-08T20:30:00",
                            close_tab_after=True,
                            check_wait_seconds=30,
                        )
                    )
                )

        self.assertTrue(result.ok)
        self.assertTrue(result.schedule_applied)
        self.assertEqual(result.scheduled_at, "2026-04-08T20:30:00")
        self.assertEqual(mock_upload.call_args.kwargs["schedule_at"].isoformat(), "2026-04-08T20:30:00")
        self.assertTrue(mock_upload.call_args.kwargs["close_upload_tab_after"])
        self.assertEqual(mock_upload.call_args.kwargs["check_wait_seconds"], 30)

    def test_start_upload_normalizes_aware_schedule_to_local_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "clip.mp4"
            video.write_bytes(b"video")

            aware_value = "2026-04-09T13:30:00+00:00"
            expected = roxy.datetime.fromisoformat(aware_value).astimezone().replace(tzinfo=None, microsecond=0)

            with patch.object(roxy, "upload_video_via_roxy") as mock_upload:
                mock_upload.return_value = RoxyUploadSummary(
                    profile_id="profile-1",
                    video_path=video,
                    debugger_address="127.0.0.1:9222",
                    message="ok",
                    schedule_applied=True,
                    scheduled_at=expected.isoformat(),
                )
                result = roxy.asyncio.run(
                    roxy.start_upload(
                        roxy.RoxyUploadRequest(
                            api_host="http://127.0.0.1:50000",
                            api_token="token",
                            workspace_id=1,
                            profile_id="profile-1",
                            video_path=str(video),
                            schedule_at=aware_value,
                        )
                    )
                )

        self.assertTrue(result.ok)
        self.assertEqual(mock_upload.call_args.kwargs["schedule_at"].isoformat(), expected.isoformat())
