from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from autocapcut.api.routes import sora
from autocapcut.services import sora_service


class SoraRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        sora._workers.clear()
        sora._realtime._worker_clients.clear()

    def test_verify_key_accepts_generated_bridge_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "sora_key.txt"
            with patch.object(sora_service, "_KEY_PATH", key_path):
                key = sora_service.get_or_create_api_key()
                result = sora.verify_key(sora.VerifyKeyRequest(api_key=key))

        self.assertTrue(result["ok"])
        self.assertTrue(result["connected"])
        self.assertEqual(result["masked_key"], sora_service.mask_api_key(key))

    def test_verify_key_rejects_invalid_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "sora_key.txt"
            with patch.object(sora_service, "_KEY_PATH", key_path):
                sora_service.get_or_create_api_key()
                with self.assertRaises(HTTPException) as ctx:
                    sora.verify_key(sora.VerifyKeyRequest(api_key="wrong-key"))

        self.assertEqual(ctx.exception.status_code, 401)

    def test_save_uploaded_video_uses_incrementing_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            first = sora_service.save_uploaded_video(b"one", folder, suffix=".mp4")
            second = sora_service.save_uploaded_video(b"two", folder, suffix=".mp4")

        self.assertEqual(first.name, "video_001.mp4")
        self.assertEqual(second.name, "video_002.mp4")

    def test_save_uploaded_video_uses_job_index_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            second = sora_service.save_uploaded_video(b"two", folder, suffix=".mp4", output_index=2)
            duplicate = sora_service.save_uploaded_video(b"two-b", folder, suffix=".mp4", output_index=2)

        self.assertEqual(second.name, "video_002.mp4")
        self.assertEqual(duplicate.name, "video_002_02.mp4")

    def test_sora_output_folder_inspect_and_override_clear_managed_videos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "video_001.mp4").write_bytes(b"old")
            (folder / "raw_clip.mov").write_bytes(b"keep")

            inspect = sora.asyncio.run(sora.inspect_output_folder(sora.OutputFolderInspectRequest(folder=str(folder))))
            init = sora.asyncio.run(sora.init_output_folder(sora.OutputFolderInitRequest(folder=str(folder), conflict_mode="replace_all")))

            self.assertEqual(inspect["video_count"], 2)
            self.assertEqual(inspect["video_sample_names"], ["raw_clip.mov", "video_001.mp4"])
            self.assertEqual(init["removed_video_count"], 1)
            self.assertFalse((folder / "video_001.mp4").exists())
            self.assertTrue((folder / "raw_clip.mov").exists())

    def test_job_done_prefers_no_watermark_url(self) -> None:
        job = SimpleNamespace(
            job_index=1,
            child_project_id=99,
            ratio="16:9",
            status="generating",
            progress=0,
            video_path=None,
            generation_id=None,
            permalink=None,
            updated_at=None,
            error_msg=None,
        )
        child = SimpleNamespace(base_folder_path="/tmp/sora-test")
        session = AsyncMock()
        session.get = AsyncMock(side_effect=[job, child])
        session.add = Mock()

        body = sora.JobDoneRequest(
            job_id=7,
            no_watermark_url="https://cdn.example.com/no-watermark.mp4",
            downloadable_url="https://cdn.example.com/watermark.mp4",
            generation_id="gen_123",
            public_permalink="https://sora.example.com/post/123",
        )

        raw_path = Path("/tmp/sora-raw/video_001.mp4")
        saved_path = Path("/tmp/sora-test/video_001.mp4")
        with patch.object(sora, "download_video", AsyncMock(return_value=raw_path)) as mock_download:
            with patch.object(sora, "enhance_video_1080p", Mock(return_value=saved_path)) as mock_enhance:
                with patch.object(sora, "next_video_path", Mock(return_value=saved_path)):
                    result = sora.asyncio.run(sora.job_done(body, session))

        download_args = mock_download.await_args
        self.assertEqual(download_args.args[0], "https://cdn.example.com/no-watermark.mp4")
        self.assertEqual(download_args.kwargs["output_index"], 1)
        mock_enhance.assert_called_once_with(raw_path, ratio="16:9", destination_path=saved_path)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["video_path"], str(saved_path))
        self.assertEqual(job.status, "done")
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.video_path, str(saved_path))
        self.assertEqual(job.generation_id, "gen_123")
        self.assertEqual(job.permalink, "https://sora.example.com/post/123")

    def test_target_resolution_for_ratio(self) -> None:
        self.assertEqual(sora_service.target_resolution_for_ratio("16:9"), (1920, 1080))
        self.assertEqual(sora_service.target_resolution_for_ratio("9:16"), (1080, 1920))
        self.assertEqual(sora_service.target_resolution_for_ratio("1:1"), (1080, 1080))

    def test_download_job_file_returns_saved_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saved_path = Path(temp_dir) / "video001.mp4"
            saved_path.write_bytes(b"mp4")

            job = SimpleNamespace(video_path=str(saved_path))
            session = AsyncMock()
            session.get = AsyncMock(return_value=job)

            response = sora.asyncio.run(sora.download_job_file(10, session))

        self.assertEqual(Path(response.path), saved_path)
        self.assertEqual(response.filename, "video001.mp4")

    def test_connection_status_counts_realtime_worker_socket(self) -> None:
        sora._realtime._worker_clients["worker-live"] = object()

        result = sora.connection_status()

        self.assertTrue(result["online"])
        self.assertEqual(result["worker_count"], 1)

    def test_get_job_detail_returns_serialized_job(self) -> None:
        job = SimpleNamespace(
            id=7,
            job_index=3,
            prompt="a whale",
            ratio="16:9",
            duration=5,
            status="failed",
            progress=0,
            video_path=None,
            generation_id="gen_7",
            permalink="https://example.com/post/7",
            error_msg="Stopped by user.",
        )
        session = AsyncMock()
        session.get = AsyncMock(return_value=job)

        result = sora.asyncio.run(sora.get_job_detail(7, session))

        self.assertEqual(result["job"]["id"], 7)
        self.assertEqual(result["job"]["job_index"], 3)
        self.assertEqual(result["job"]["prompt"], "a whale")
        self.assertEqual(result["job"]["status"], "failed")
        self.assertEqual(result["job"]["error_msg"], "Stopped by user.")

    def test_open_output_folder_uses_mac_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(sora.sys, "platform", "darwin"):
                with patch.object(sora.subprocess, "Popen") as mock_popen:
                    result = sora.asyncio.run(
                        sora.open_output_folder(sora.OutputFolderOpenRequest(folder=temp_dir))
                    )

        self.assertTrue(result["ok"])
        self.assertEqual(result["folder"], temp_dir)
        mock_popen.assert_called_once_with(["open", temp_dir])
