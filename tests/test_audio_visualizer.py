from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

from autocapcut.services.audio_visualizer import (
    PREVIEW_SPECTRUM_CANVAS_H,
    PREVIEW_SPECTRUM_CANVAS_W,
    _jobs,
    JobStatus,
    VisualizerConfig,
    build_ffmpeg_command,
    build_spectrum_pipe_command,
    build_spectrum_composite_command,
    render_visualizer,
)
from autocapcut.services.audio_visualizer_spectrum import build_spectrum_layout, iter_spectrum_states
from autocapcut.services.rust_audio_renderer import (
    find_rust_audio_renderer_binary,
    resolve_audio_visualizer_engine,
)


class AudioVisualizerRenderTests(unittest.TestCase):
    def test_spectrum_render_command_keeps_mirrored_bar_shape(self) -> None:
        config = VisualizerConfig(
            style="spectrum_bars",
            resolution="1920x1080",
            output_format="mp4",
        )

        with patch(
            "autocapcut.services.audio_visualizer._has_encoder",
            side_effect=lambda name: name == "h264_videotoolbox",
        ):
            cmd = build_ffmpeg_command("input.mp3", "output.mp4", config, 12.0)

        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_videotoolbox")

        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("showfreqs=s=", filter_complex)
        self.assertIn("mode=bar", filter_complex)
        self.assertIn("drawgrid=w=", filter_complex)
        self.assertIn("split=4[vis_top][vis_bottom_src][vis_top_glow_src][vis_bottom_glow_src]", filter_complex)
        self.assertIn("[vis_bottom_src]vflip[vis_bottom]", filter_complex)
        self.assertIn("[vis_top_glow_src]gblur=sigma=10", filter_complex)
        self.assertIn("[vis_bottom_glow_src]vflip,gblur=sigma=10", filter_complex)
        self.assertIn("drawbox=x=", filter_complex)
        self.assertIn("[spec_bg_3][vis_bottom]overlay=", filter_complex)

    def test_spectrum_render_command_falls_back_to_libx264_when_vtb_missing(self) -> None:
        config = VisualizerConfig(
            style="spectrum_bars",
            resolution="1920x1080",
            output_format="mp4",
        )

        with patch(
            "autocapcut.services.audio_visualizer._has_encoder",
            return_value=False,
        ):
            cmd = build_ffmpeg_command("input.mp3", "output.mp4", config, 12.0)

        self.assertEqual(cmd[cmd.index("-c:v") + 1], "libx264")
        self.assertIn("-preset", cmd)
        self.assertIn("-crf", cmd)

    def test_iter_spectrum_states_handles_empty_audio(self) -> None:
        layout = build_spectrum_layout(1920, 1080)

        with patch(
            "autocapcut.services.audio_visualizer_spectrum.decode_audio_mono",
            return_value=np.array([], dtype=np.float32),
        ):
            frames = list(iter_spectrum_states("input.mp3", layout, 30))

        self.assertEqual(len(frames), 1)
        total_frames, bars, peaks = frames[0]
        self.assertEqual(total_frames, 1)
        self.assertEqual(bars.shape[0], layout.bins)
        self.assertEqual(peaks.shape[0], layout.bins)

    def test_spectrum_composite_scales_internal_overlay_up_to_display_layout(self) -> None:
        config = VisualizerConfig(
            style="spectrum_bars",
            resolution="1920x1080",
            output_format="mp4",
        )
        display_layout = build_spectrum_layout(1920, 1080)
        source_layout = build_spectrum_layout(1280, 720)

        with patch(
            "autocapcut.services.audio_visualizer._has_encoder",
            side_effect=lambda name: name == "h264_videotoolbox",
        ):
            cmd = build_spectrum_composite_command(
                audio_path="input.mp3",
                overlay_path="overlay.mp4",
                layout=display_layout,
                output_path="output.mp4",
                config=config,
                duration_s=20.0,
                source_layout=source_layout,
            )

        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn(f"scale={display_layout.overlay_width}:{display_layout.overlay_height}:flags=lanczos", filter_complex)

    def test_spectrum_pipe_command_uses_rawvideo_input_and_scaling(self) -> None:
        config = VisualizerConfig(
            style="spectrum_bars",
            resolution="1920x1080",
            output_format="mp4",
        )
        display_layout = build_spectrum_layout(1920, 1080)
        source_layout = build_spectrum_layout(960, 540)

        with patch(
            "autocapcut.services.audio_visualizer._has_encoder",
            side_effect=lambda name: name == "h264_videotoolbox",
        ):
            cmd = build_spectrum_pipe_command(
                audio_path="input.mp3",
                source_layout=source_layout,
                display_layout=display_layout,
                output_path="output.mp4",
                config=config,
                duration_s=20.0,
            )

        self.assertIn("pipe:0", cmd)
        self.assertIn("-f", cmd)
        self.assertIn("rawvideo", cmd)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn(f"scale={display_layout.overlay_width}:{display_layout.overlay_height}:flags=lanczos", filter_complex)
        self.assertIn("format=rgba,colorkey=0x000000:0.08:0.0[spec_rgba]", filter_complex)


class RustAudioRendererTests(unittest.IsolatedAsyncioTestCase):
    def test_resolve_audio_visualizer_engine_invalid_defaults_to_auto(self) -> None:
        with patch.dict("os.environ", {"AUTOCAPCUT_AUDIO_VIS_ENGINE": "weird"}, clear=False):
            self.assertEqual(resolve_audio_visualizer_engine(), "auto")

    def test_find_rust_audio_renderer_binary_prefers_custom_path(self) -> None:
        fake_bin = Path("/tmp/fake_audio_renderer")
        with patch.dict("os.environ", {"AUTOCAPCUT_AUDIO_VIS_ENGINE_BIN": str(fake_bin)}, clear=False):
            with patch.object(Path, "exists", return_value=True), patch.object(Path, "is_file", return_value=True):
                self.assertEqual(find_rust_audio_renderer_binary(), fake_bin)

    async def test_render_visualizer_uses_rust_renderer_only_when_explicitly_enabled(self) -> None:
        job_id = "job-rust-render"
        _jobs[job_id] = JobStatus(job_id=job_id)
        config = VisualizerConfig(style="spectrum_bars", resolution="1920x1080", output_format="mp4")

        try:
            with patch(
                "autocapcut.services.audio_visualizer.get_audio_duration",
                new=AsyncMock(return_value=1.0),
            ), patch(
                "autocapcut.services.audio_visualizer.build_spectrum_pipe_command",
                return_value=["ffmpeg", "-version"],
            ), patch(
                "autocapcut.services.audio_visualizer.resolve_audio_visualizer_engine",
                return_value="rust",
            ), patch(
                "autocapcut.services.audio_visualizer.find_rust_audio_renderer_binary",
                return_value=Path("/tmp/audio_spectrum_renderer"),
            ), patch(
                "autocapcut.services.audio_visualizer.render_spectrum_final_video_rust",
            ) as rust_render, patch(
                "autocapcut.services.audio_visualizer.render_spectrum_final_video",
            ) as py_render:
                await render_visualizer(job_id, "input.mp3", config)

            rust_render.assert_called_once()
            py_render.assert_not_called()
            self.assertEqual(_jobs[job_id].status, "done")
        finally:
            _jobs.pop(job_id, None)

    async def test_render_visualizer_uses_display_layout_for_preview_parity(self) -> None:
        job_id = "job-preview-parity"
        _jobs[job_id] = JobStatus(job_id=job_id)
        config = VisualizerConfig(style="spectrum_bars", resolution="1920x1080", output_format="mp4")
        captured: dict[str, object] = {}

        def _capture_pipe(*args, **kwargs):
            captured["source_layout"] = kwargs["source_layout"]
            captured["display_layout"] = kwargs["display_layout"]
            return ["ffmpeg", "-version"]

        def _capture_render(*args):
            captured["render_width"] = args[2]
            captured["render_height"] = args[3]
            return None

        try:
            with patch(
                "autocapcut.services.audio_visualizer.get_audio_duration",
                new=AsyncMock(return_value=1.0),
            ), patch(
                "autocapcut.services.audio_visualizer.build_spectrum_pipe_command",
                side_effect=_capture_pipe,
            ), patch(
                "autocapcut.services.audio_visualizer.resolve_audio_visualizer_engine",
                return_value="auto",
            ), patch(
                "autocapcut.services.audio_visualizer.render_spectrum_final_video",
                side_effect=_capture_render,
            ), patch(
                "autocapcut.services.audio_visualizer.render_spectrum_final_video_rust",
            ) as rust_render:
                await render_visualizer(job_id, "input.mp3", config)

            rust_render.assert_not_called()
            preview_layout = build_spectrum_layout(PREVIEW_SPECTRUM_CANVAS_W, PREVIEW_SPECTRUM_CANVAS_H)
            self.assertEqual(captured["render_width"], preview_layout.overlay_width)
            self.assertEqual(captured["render_height"], preview_layout.overlay_height)
            self.assertEqual(
                getattr(captured["source_layout"], "overlay_width"),
                preview_layout.overlay_width,
            )
            self.assertEqual(
                getattr(captured["source_layout"], "overlay_height"),
                preview_layout.overlay_height,
            )
            self.assertNotEqual(
                getattr(captured["source_layout"], "overlay_width"),
                getattr(captured["display_layout"], "overlay_width"),
            )
            self.assertEqual(_jobs[job_id].status, "done")
        finally:
            _jobs.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
