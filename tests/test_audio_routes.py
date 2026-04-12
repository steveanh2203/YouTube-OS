from __future__ import annotations

import asyncio
import base64
from tempfile import TemporaryDirectory
import unittest
import zipfile
from pathlib import Path

from autocapcut.api.routes.audio import (
    AudioZipItem,
    AudioZipSaveRequest,
    _build_zip_archive,
    _sanitize_archive_name,
    save_audio_zip,
)


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


class AudioRouteTests(unittest.TestCase):
    def test_build_zip_archive_sanitizes_and_dedupes_names(self) -> None:
        with TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "audio_bundle.zip"
            items = [
                AudioZipItem(filename="../audio_001.mp3", content_base64=_b64(b"first")),
                AudioZipItem(filename="audio_001.mp3", content_base64=_b64(b"second")),
            ]

            _build_zip_archive(archive_path, items)

            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.namelist(), ["audio_001.mp3", "audio_001_2.mp3"])
                self.assertEqual(archive.read("audio_001.mp3"), b"first")
                self.assertEqual(archive.read("audio_001_2.mp3"), b"second")

    def test_sanitize_archive_name_rejects_empty_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid archive filename"):
            _sanitize_archive_name("   ")

    def test_save_audio_zip_appends_zip_extension(self) -> None:
        with TemporaryDirectory() as temp_dir:
            response = asyncio.run(save_audio_zip(
                AudioZipSaveRequest(
                    save_path=str(Path(temp_dir) / "session-audio"),
                    files=[
                        AudioZipItem(filename="audio_001.mp3", content_base64=_b64(b"clip")),
                    ],
                ),
            ))

            self.assertTrue(response.ok)
            self.assertIsNotNone(response.path)
            saved_path = Path(response.path or "")
            self.assertEqual(saved_path.suffix, ".zip")
            self.assertTrue(saved_path.exists())
