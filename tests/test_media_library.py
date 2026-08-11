from __future__ import annotations

from io import BytesIO
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel


class MediaWorkspaceTests(unittest.TestCase):
    def test_workspace_references_resolve_directories_and_files_without_escape(self) -> None:
        from autocapcut.services.media_workspace import (
            UnsafeMediaPath,
            create_workspace_directory,
            resolve_workspace_file_reference,
            resolve_workspace_or_local,
            workspace_file_reference,
        )

        with TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir},
        ):
            directory = create_workspace_directory("Batch One")
            resolved_directory = resolve_workspace_or_local(directory.reference)
            video = resolved_directory / "renders" / "clip.mp4"
            video.parent.mkdir()
            video.write_bytes(b"video")

            reference = workspace_file_reference(directory.reference, video)

            self.assertEqual(resolve_workspace_file_reference(reference), video.resolve())
            self.assertTrue(reference.startswith("workspace-file:"))
            with self.assertRaises(UnsafeMediaPath):
                resolve_workspace_file_reference(
                    f"workspace-file:{directory.reference.removeprefix('workspace:')}/../outside.mp4"
                )
            with self.assertRaises(UnsafeMediaPath):
                from autocapcut.services.media_workspace import store_workspace_file
                store_workspace_file(directory.reference, "/absolute.mp4", BytesIO(b"video"))

    def test_store_upload_sanitizes_name_and_keeps_file_inside_workspace(self) -> None:
        from autocapcut.services.media_workspace import store_upload

        with TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir},
        ):
            stored = store_upload("../../voice.mp3", BytesIO(b"audio"), "audio/mpeg")

            root = Path(temp_dir).resolve()
            path = Path(stored.path).resolve()
            self.assertTrue(path.is_relative_to(root))
            self.assertEqual(path.name, "voice.mp3")
            self.assertEqual(path.read_bytes(), b"audio")
            self.assertEqual(stored.kind, "audio")
            self.assertEqual(stored.size_bytes, 5)

    def test_store_upload_rejects_unsupported_extensions(self) -> None:
        from autocapcut.services.media_workspace import UnsupportedMediaType, store_upload

        with TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir},
        ):
            with self.assertRaises(UnsupportedMediaType):
                store_upload("payload.exe", BytesIO(b"unsafe"), "application/octet-stream")

    def test_resolve_managed_path_rejects_files_outside_workspace(self) -> None:
        from autocapcut.services.media_workspace import UnsafeMediaPath, resolve_managed_path

        with TemporaryDirectory() as temp_dir, TemporaryDirectory() as outside_dir, patch.dict(
            "os.environ",
            {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir},
        ):
            outside = Path(outside_dir) / "outside.mp3"
            outside.write_bytes(b"audio")

            with self.assertRaises(UnsafeMediaPath):
                resolve_managed_path(outside)


class MediaRoutesTests(unittest.TestCase):
    def test_upload_list_stream_and_delete_asset(self) -> None:
        from autocapcut.api.routes import audio_visualizer, media, srt
        from autocapcut.database.connection import get_session
        from autocapcut.database import models  # noqa: F401

        with TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {"AUTOCAPCUT_WORKSPACE_DIR": str(Path(temp_dir) / "workspace")},
        ):
            db_path = (Path(temp_dir) / "media.db").as_posix()
            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

            async def prepare_database() -> None:
                async with engine.begin() as connection:
                    await connection.run_sync(SQLModel.metadata.create_all)

            asyncio.run(prepare_database())

            async def session_override():
                async with AsyncSession(engine, expire_on_commit=False) as session:
                    yield session

            app = FastAPI()
            app.include_router(media.router, prefix="/api/media")
            app.include_router(audio_visualizer.router, prefix="/api/audio-visualizer")
            app.include_router(srt.router, prefix="/api/srt")
            app.dependency_overrides[get_session] = session_override

            with TestClient(app) as client:
                created_directory = client.post("/api/media/directories", json={"name": "Video 01"})
                self.assertEqual(created_directory.status_code, 201)
                directory = created_directory.json()
                self.assertTrue(directory["reference"].startswith("workspace:"))
                self.assertNotIn("path", directory)
                self.assertEqual(client.get("/api/media/directories").json()[0]["reference"], directory["reference"])
                resolved_directory = media.resolve_workspace_reference(directory["reference"])
                self.assertTrue(resolved_directory.is_dir())
                self.assertTrue(resolved_directory.is_relative_to((Path(temp_dir) / "workspace").resolve()))

                imported = client.post(
                    f"/api/media/directories/{directory['reference'].removeprefix('workspace:')}/files",
                    files=[
                        ("files", ("voice.mp3", b"audio", "audio/mpeg")),
                        ("files", ("cover.jpg", b"image", "image/jpeg")),
                    ],
                    data={"relative_paths": ["audio/voice.mp3", "images/cover.jpg"]},
                )
                self.assertEqual(imported.status_code, 201, imported.text)
                self.assertEqual(imported.json()["imported"], 2)
                self.assertEqual((resolved_directory / "audio" / "voice.mp3").read_bytes(), b"audio")
                self.assertEqual((resolved_directory / "images" / "cover.jpg").read_bytes(), b"image")
                imported_audio = imported.json()["files"][0]
                downloaded_workspace_file = client.get(
                    "/api/media/files/download",
                    params={"reference": imported_audio["reference"]},
                )
                self.assertEqual(downloaded_workspace_file.status_code, 200)
                self.assertEqual(downloaded_workspace_file.content, b"audio")
                self.assertIn("voice.mp3", downloaded_workspace_file.headers["content-disposition"])

                escaped = client.post(
                    f"/api/media/directories/{directory['reference'].removeprefix('workspace:')}/files",
                    files={"files": ("evil.mp3", b"bad", "audio/mpeg")},
                    data={"relative_paths": "../evil.mp3"},
                )
                self.assertEqual(escaped.status_code, 422)

                upload = client.post(
                    "/api/media/assets",
                    files={"file": ("voice.mp3", b"audio-bytes", "audio/mpeg")},
                )
                self.assertEqual(upload.status_code, 201)
                asset = upload.json()
                self.assertEqual(asset["original_name"], "voice.mp3")
                self.assertEqual(asset["kind"], "audio")
                self.assertEqual(asset["reference"], f"media:{asset['id']}")
                self.assertNotIn("path", asset)

                listed = client.get("/api/media/assets")
                self.assertEqual(listed.status_code, 200)
                self.assertEqual([item["id"] for item in listed.json()], [asset["id"]])

                streamed = client.get(asset["content_url"])
                self.assertEqual(streamed.status_code, 200)
                self.assertEqual(streamed.content, b"audio-bytes")

                async def resolve_reference() -> Path:
                    async with AsyncSession(engine, expire_on_commit=False) as session:
                        return await media.resolve_media_reference(asset["reference"], session)

                resolved = asyncio.run(resolve_reference())
                self.assertTrue(resolved.is_file())
                self.assertTrue(resolved.is_relative_to((Path(temp_dir) / "workspace").resolve()))

                with patch.object(audio_visualizer, "create_job", return_value="job-1"), patch.object(
                    audio_visualizer,
                    "render_visualizer",
                    new=AsyncMock(),
                ) as render_mock:
                    render = client.post(
                        "/api/audio-visualizer/render",
                        json={"audio_path": asset["reference"], "config": {"style": "spectrum_bars"}},
                    )
                    self.assertEqual(render.status_code, 202)
                    render_mock.assert_awaited_once()
                    self.assertEqual(Path(render_mock.await_args.args[1]), resolved)

                subtitle_upload = client.post(
                    "/api/media/assets",
                    files={"file": ("captions.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHello\n", "text/plain")},
                ).json()
                with patch.object(srt, "generate_merged_srt", return_value="merged") as merge_mock:
                    generated = client.post(
                        "/api/srt/generate",
                        json={"srt_path": subtitle_upload["reference"], "content_text": "Hello"},
                    )
                    self.assertEqual(generated.status_code, 200)
                    self.assertEqual(generated.json()["srt_output"], "merged")
                    self.assertTrue(Path(merge_mock.call_args.args[0]).is_relative_to((Path(temp_dir) / "workspace").resolve()))

                deleted = client.delete(f"/api/media/assets/{asset['id']}")
                self.assertEqual(deleted.status_code, 204)
                client.delete(f"/api/media/assets/{subtitle_upload['id']}")
                self.assertEqual(client.get("/api/media/assets").json(), [])

            asyncio.run(engine.dispose())


if __name__ == "__main__":
    unittest.main()
