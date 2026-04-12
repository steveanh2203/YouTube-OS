from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from autocapcut.services import roxy_upload


class _FakeService:
    def stop(self) -> None:
        return None


class _FakeDriver:
    def __init__(self) -> None:
        self.service = _FakeService()


class _FakeWebDriverModule:
    class ChromeOptions:
        def add_experimental_option(self, *_args, **_kwargs) -> None:
            return None

    def __init__(self, driver: _FakeDriver) -> None:
        self._driver = driver

    def Chrome(self, *_, **__) -> _FakeDriver:
        return self._driver


class RoxyUploadServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        roxy_upload._PROFILE_SESSIONS.clear()

    def test_youtube_schedule_date_matches_visible_dropdown_label(self) -> None:
        formatted = roxy_upload._format_youtube_schedule_date(datetime(2026, 4, 9, 20, 30, 0))
        self.assertEqual(formatted, "Apr 9, 2026")

    def test_resolve_open_profile_session_reuses_same_profile_connection(self) -> None:
        client = Mock()
        client.base_url = "http://127.0.0.1:50000"
        client.open_profile.return_value = {
            "http": "127.0.0.1:9222",
            "driver": "/tmp/chromedriver",
        }

        session_a, reused_a, _key_a = roxy_upload._resolve_open_profile_session(
            client=client,
            workspace_id=1,
            profile_id="profile-1",
            force_open=True,
        )
        session_b, reused_b, _key_b = roxy_upload._resolve_open_profile_session(
            client=client,
            workspace_id=1,
            profile_id="profile-1",
            force_open=True,
        )

        self.assertFalse(reused_a)
        self.assertTrue(reused_b)
        self.assertEqual(session_a.debugger_address, "127.0.0.1:9222")
        self.assertIs(session_a, session_b)
        client.open_profile.assert_called_once()

    def test_open_schedule_section_prefers_selenium_expand_button(self) -> None:
        fake_driver = _FakeDriver()

        with patch.object(roxy_upload, "_click_schedule_expand_button", return_value=True) as mock_expand:
            result = roxy_upload._open_schedule_section(fake_driver, by=object())

        self.assertTrue(result)
        mock_expand.assert_called_once()

    def test_advance_to_visibility_uses_dedicated_next_button(self) -> None:
        fake_driver = _FakeDriver()

        with patch.object(roxy_upload, "_wait_until", return_value=True), \
            patch.object(roxy_upload, "_has_details_step_ready", return_value=True), \
            patch.object(roxy_upload, "_has_visibility_step", side_effect=[False, False, False, True]), \
            patch.object(roxy_upload, "_upload_next_button_ready", return_value=True), \
            patch.object(roxy_upload, "_click_upload_next_button", return_value=True) as mock_click:
            roxy_upload._advance_to_visibility_step(fake_driver, progress_cb=None)

        self.assertGreaterEqual(mock_click.call_count, 1)

    def test_apply_schedule_stops_if_schedule_section_never_expands(self) -> None:
        fake_driver = _FakeDriver()

        with patch.object(roxy_upload, "_wait_until", side_effect=[True, False]), \
            patch.object(roxy_upload, "_open_schedule_section", return_value=True), \
            patch.object(roxy_upload, "_set_schedule_fields") as mock_set_fields, \
            patch.object(roxy_upload, "_click_schedule_submit") as mock_submit:
            with self.assertRaises(roxy_upload.RoxyUploadError) as ctx:
                roxy_upload._apply_schedule_to_youtube(
                    fake_driver,
                    schedule_at=datetime(2026, 4, 9, 20, 30, 0),
                    progress_cb=None,
                )

        self.assertIn("did not expand", str(ctx.exception))
        mock_set_fields.assert_not_called()
        mock_submit.assert_not_called()

    def test_apply_schedule_waits_for_dedicated_done_button(self) -> None:
        fake_driver = _FakeDriver()

        with patch.object(roxy_upload, "_wait_until", side_effect=[True, True, True, True]), \
            patch.object(roxy_upload, "_open_schedule_section", return_value=True), \
            patch.object(roxy_upload, "_set_schedule_fields", return_value=(True, True)), \
            patch.object(roxy_upload, "_schedule_submit_button_ready", return_value=True), \
            patch.object(roxy_upload, "_click_schedule_submit", return_value=True) as mock_submit:
            roxy_upload._apply_schedule_to_youtube(
                fake_driver,
                schedule_at=datetime(2026, 4, 9, 20, 30, 0),
                progress_cb=None,
            )

        mock_submit.assert_called_once()

    def test_upload_stops_if_youtube_does_not_accept_selected_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "clip.mp4"
            video.write_bytes(b"video")

            fake_driver = _FakeDriver()
            fake_input = Mock()

            with patch.object(roxy_upload, "RoxyApiClient") as mock_client_cls, \
                patch.object(roxy_upload, "_require_selenium") as mock_require, \
                patch.object(roxy_upload, "_open_fresh_upload_tab", return_value=("tab-1", ["root-tab"])), \
                patch.object(roxy_upload, "_prepare_youtube_upload_page", return_value=fake_input), \
                patch.object(
                    roxy_upload,
                    "_wait_for_uploaded_file_to_attach",
                    side_effect=roxy_upload.RoxyUploadError("YouTube Studio did not accept the selected file."),
                ), \
                patch.object(roxy_upload, "_advance_to_visibility_step") as mock_advance, \
                patch.object(roxy_upload, "_apply_schedule_to_youtube") as mock_schedule:
                mock_client = mock_client_cls.return_value
                mock_client.open_profile.return_value = {
                    "http": "127.0.0.1:9222",
                    "driver": "/tmp/chromedriver",
                }
                mock_require.return_value = (_FakeWebDriverModule(fake_driver), lambda *_a, **_k: object(), object())

                with self.assertRaises(roxy_upload.RoxyUploadError) as ctx:
                    roxy_upload.upload_video_via_roxy(
                        api_host="http://127.0.0.1:50000",
                        api_token="token",
                        workspace_id=1,
                        profile_id="profile-1",
                        video_path=video,
                        schedule_at=datetime(2026, 4, 9, 20, 30, 0),
                    )

        self.assertIn("did not accept the selected file", str(ctx.exception))
        fake_input.send_keys.assert_called_once()
        mock_advance.assert_not_called()
        mock_schedule.assert_not_called()

    def test_upload_reuses_cached_open_profile_for_same_lane_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "clip.mp4"
            video.write_bytes(b"video")

            fake_driver = _FakeDriver()
            fake_input = Mock()
            roxy_upload._PROFILE_SESSIONS[("http://127.0.0.1:50000", 1, "profile-1")] = roxy_upload.RoxyBrowserSession(
                debugger_address="127.0.0.1:9222",
                driver_path="/tmp/chromedriver",
                opened_at=1.0,
                last_used_at=1.0,
            )

            with patch.object(roxy_upload, "RoxyApiClient") as mock_client_cls, \
                patch.object(roxy_upload, "_require_selenium") as mock_require, \
                patch.object(roxy_upload, "_open_fresh_upload_tab", return_value=("tab-1", ["root-tab"])), \
                patch.object(roxy_upload, "_prepare_youtube_upload_page", return_value=fake_input), \
                patch.object(roxy_upload, "_wait_for_uploaded_file_to_attach"), \
                patch.object(roxy_upload, "_advance_to_visibility_step"), \
                patch.object(roxy_upload, "_apply_schedule_to_youtube"):
                mock_client = mock_client_cls.return_value
                mock_client.base_url = "http://127.0.0.1:50000"
                mock_require.return_value = (_FakeWebDriverModule(fake_driver), lambda *_a, **_k: object(), object())

                roxy_upload.upload_video_via_roxy(
                    api_host="http://127.0.0.1:50000",
                    api_token="token",
                    workspace_id=1,
                    profile_id="profile-1",
                    video_path=video,
                    schedule_at=datetime(2026, 4, 9, 20, 30, 0),
                )

        mock_client.open_profile.assert_not_called()
