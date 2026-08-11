from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch


class WebWorkspaceRouteTests(unittest.TestCase):
    def test_ffmpeg_routes_resolve_workspace_directories_before_services(self) -> None:
        from autocapcut.api.routes import cut_automate, fast_edit
        from autocapcut.services.media_workspace import create_workspace_directory, resolve_workspace_reference

        with TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir}):
            directory = create_workspace_directory("FFmpeg inputs")
            expected = str(resolve_workspace_reference(directory.reference))

            payload = cut_automate._resolve_directory_payload({
                "input_dir": directory.reference,
                "output_dir": directory.reference,
                "segment_time": 10,
            })
            self.assertEqual(payload["input_dir"], expected)
            self.assertEqual(payload["output_dir"], expected)

            renamer = Mock()
            renamer.get_file_count.return_value = {"total": 0}
            with patch.object(fast_edit, "_get_renamer", return_value=renamer):
                asyncio.run(fast_edit.file_count(fast_edit.FileCountRequest(directory=directory.reference)))
            renamer.get_file_count.assert_called_once_with(expected, "audio")

            rendered = resolve_workspace_reference(directory.reference) / "rendered.mp4"
            rendered.write_bytes(b"video")
            output_reference = fast_edit._output_reference(directory.reference, str(rendered))
            self.assertTrue(output_reference.startswith("workspace-file:"))

            opaque_result = cut_automate._opaque_result(
                {"outputs": [str(rendered)]},
                {expected: directory.reference},
            )
            self.assertTrue(opaque_result["outputs"][0].startswith("workspace-file:"))

    def test_ai_seo_and_livestream_routes_accept_workspace_references(self) -> None:
        from autocapcut.api.routes import ai_gen, livestream
        from autocapcut.services.media_workspace import create_workspace_directory, resolve_workspace_reference

        with TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"AUTOCAPCUT_WORKSPACE_DIR": temp_dir}):
            directory = create_workspace_directory("Generated output")
            resolved = resolve_workspace_reference(directory.reference)
            (resolved / "clip.mp4").write_bytes(b"video")

            self.assertEqual(ai_gen._normalize_output_folder(directory.reference), str(resolved))
            videos = livestream._gather_videos({"video_dirs": [directory.reference], "shuffle": False})
            self.assertEqual(videos, [resolved / "clip.mp4"])


if __name__ == "__main__":
    unittest.main()
