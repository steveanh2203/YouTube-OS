from __future__ import annotations

import asyncio
import base64
import unittest
import zipfile
from io import BytesIO

from autocapcut.api.routes.audio import (
    AudioZipItem,
    _sanitize_archive_name,
    create_audio_zip,
    router,
)


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


class AudioRouteTests(unittest.TestCase):
    def test_create_audio_zip_returns_browser_download(self) -> None:
        response = asyncio.run(create_audio_zip([
            AudioZipItem(filename="audio_001.mp3", content_base64=_b64(b"clip")),
        ]))

        self.assertEqual(response.media_type, "application/zip")
        self.assertIn("attachment", response.headers["content-disposition"])
        with zipfile.ZipFile(BytesIO(response.body)) as archive:
            self.assertEqual(archive.read("audio_001.mp3"), b"clip")

    def test_sanitize_archive_name_rejects_empty_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid archive filename"):
            _sanitize_archive_name("   ")

    def test_desktop_save_path_routes_are_not_exposed(self) -> None:
        paths = {route.path for route in router.routes}
        self.assertNotIn("/save", paths)
        self.assertNotIn("/save-zip", paths)
