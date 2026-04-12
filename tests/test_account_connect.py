from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from autocapcut.services import account_connect


class AccountConnectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get(account_connect.ACCOUNT_CONNECT_FILE_ENV)
        os.environ[account_connect.ACCOUNT_CONNECT_FILE_ENV] = str(Path(self._tmpdir.name) / "account-connect.json")

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop(account_connect.ACCOUNT_CONNECT_FILE_ENV, None)
        else:
            os.environ[account_connect.ACCOUNT_CONNECT_FILE_ENV] = self._old_env
        self._tmpdir.cleanup()

    def test_bridge_key_is_generated_and_validates(self) -> None:
        settings = account_connect.get_bridge_settings()

        self.assertTrue(settings.bridge_key)
        self.assertTrue(account_connect.is_bridge_key_valid(settings.bridge_key))
        self.assertFalse(account_connect.is_bridge_key_valid("wrong-key"))

    def test_save_and_load_youtube_config(self) -> None:
        saved = account_connect.save_youtube_config("12", {
            "access_token": "token-123",
            "refresh_token": "refresh-123",
            "token_expiry": "2026-04-01T05:00:00Z",
            "channel_id": "UC123",
            "channel_name": "SeniorForge 365",
            "openrouter_key": "sk-or-v1-abc",
            "model": "openrouter/free",
            "system_prompt": "Reply politely.",
            "channel_dna": "Trustworthy health educator.",
            "audience_profile": "Adults over 60 looking for simple health tips.",
            "target_market": "US senior health audience.",
            "auto_reply_mode": "safe-only",
            "provider_mode": "api",
            "connected": True,
            "verified_at": "2026-03-31T04:00:00Z",
        })

        self.assertEqual(saved["channel_id"], "UC123")

        loaded = account_connect.get_youtube_config("12")
        self.assertEqual(loaded["access_token"], "token-123")
        self.assertEqual(loaded["refresh_token"], "refresh-123")
        self.assertEqual(loaded["channel_name"], "SeniorForge 365")
        self.assertEqual(loaded["system_prompt"], "Reply politely.")
        self.assertEqual(loaded["channel_dna"], "Trustworthy health educator.")
        self.assertEqual(loaded["audience_profile"], "Adults over 60 looking for simple health tips.")
        self.assertEqual(loaded["target_market"], "US senior health audience.")
        self.assertEqual(loaded["auto_reply_mode"], "safe-only")
        self.assertEqual(loaded["provider_mode"], "api")
        self.assertTrue(loaded["connected"])

    def test_save_verified_connection_preserves_existing_openrouter_settings(self) -> None:
        account_connect.save_youtube_config("88", {
            "openrouter_key": "sk-or-v1-keep",
            "model": "openrouter/free",
            "system_prompt": "Keep it short.",
            "channel_dna": "Warm and practical.",
            "audience_profile": "Caregivers and older adults.",
            "target_market": "US caregivers.",
            "auto_reply_mode": "safe-only",
        })

        saved = account_connect.save_verified_connection(
            parent_id="88",
            access_token="token-xyz",
            refresh_token="refresh-xyz",
            token_expiry="2026-04-01T06:00:00Z",
            channel_id="UC999",
            channel_name="Dr Pickle",
            verified_at="2026-03-31T05:00:00Z",
        )

        self.assertEqual(saved["openrouter_key"], "sk-or-v1-keep")
        self.assertEqual(saved["system_prompt"], "Keep it short.")
        self.assertEqual(saved["channel_dna"], "Warm and practical.")
        self.assertEqual(saved["audience_profile"], "Caregivers and older adults.")
        self.assertEqual(saved["target_market"], "US caregivers.")
        self.assertEqual(saved["auto_reply_mode"], "safe-only")
        self.assertEqual(saved["refresh_token"], "refresh-xyz")
        self.assertEqual(saved["channel_id"], "UC999")
        self.assertTrue(saved["connected"])

    def test_save_and_load_oauth_settings(self) -> None:
        saved = account_connect.save_oauth_settings({
            "client_id": "abc.apps.googleusercontent.com",
            "client_secret": "GOCSPX-super-secret",
            "verified_at": "2026-04-01T06:30:00Z",
        })

        self.assertEqual(saved["client_id"], "abc.apps.googleusercontent.com")
        self.assertTrue(account_connect.oauth_settings_ready(saved))

        loaded = account_connect.get_oauth_settings()
        self.assertEqual(loaded["client_secret"], "GOCSPX-super-secret")
        self.assertEqual(loaded["verified_at"], "2026-04-01T06:30:00Z")

    def test_disconnect_clears_auth_fields_but_keeps_openrouter_settings(self) -> None:
        account_connect.save_youtube_config("77", {
            "openrouter_key": "sk-or-v1-keep",
            "model": "openrouter/free",
            "system_prompt": "Stay warm.",
            "channel_dna": "Calm and reassuring.",
            "audience_profile": "Health-conscious viewers.",
            "target_market": "English speaking wellness market.",
            "auto_reply_mode": "safe-only",
        })
        account_connect.save_verified_connection(
            parent_id="77",
            access_token="token-77",
            refresh_token="refresh-77",
            token_expiry="2026-04-01T07:00:00Z",
            channel_id="UC777",
            channel_name="OAuth Channel",
            verified_at="2026-04-01T06:45:00Z",
        )

        saved = account_connect.disconnect_youtube_config("77")

        self.assertEqual(saved["openrouter_key"], "sk-or-v1-keep")
        self.assertEqual(saved["model"], "openrouter/free")
        self.assertEqual(saved["system_prompt"], "Stay warm.")
        self.assertEqual(saved["channel_dna"], "Calm and reassuring.")
        self.assertEqual(saved["audience_profile"], "Health-conscious viewers.")
        self.assertEqual(saved["target_market"], "English speaking wellness market.")
        self.assertEqual(saved["auto_reply_mode"], "safe-only")
        self.assertEqual(saved["access_token"], "")
        self.assertEqual(saved["refresh_token"], "")
        self.assertEqual(saved["channel_id"], "")
        self.assertFalse(saved["connected"])

    def test_save_and_load_youtube_inbox_cache(self) -> None:
        saved = account_connect.save_youtube_inbox_cache(
            "55",
            ok=True,
            message="Synced 1 comment.",
            synced_at="2026-03-31T12:00:00Z",
            source="extension",
            items=[{
                "thread_id": "c1",
                "comment_id": "c1",
                "video_id": "v1",
                "video_title": "Video 1",
                "author_display_name": "Viewer A",
                "author_channel_id": "UCviewer",
                "text": "hello",
                "published_at": "2026-03-31T11:59:00Z",
                "reply_count": 0,
                "is_from_channel_owner": False,
                "can_auto_reply": True,
            }],
        )

        self.assertTrue(saved["ok"])
        self.assertEqual(len(saved["items"]), 1)

        loaded = account_connect.get_youtube_inbox_cache("55")
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["message"], "Synced 1 comment.")
        self.assertEqual(loaded["items"][0]["comment_id"], "c1")

    def test_save_and_load_ai_reply_settings(self) -> None:
        saved = account_connect.save_ai_reply_settings({
            "openrouter_key": "sk-or-v1-global",
            "primary_model": "openrouter/free",
            "fallback_models": ["stepfun/step-3.5-flash:free", "openrouter/free", "minimax/minimax-m2.5:free"],
            "verified_at": "2026-04-02T03:00:00Z",
        })

        self.assertEqual(saved["openrouter_key"], "sk-or-v1-global")
        self.assertEqual(saved["primary_model"], "openrouter/free")
        self.assertEqual(
            saved["fallback_models"],
            ["stepfun/step-3.5-flash:free", "minimax/minimax-m2.5:free"],
        )
        self.assertEqual(saved["verified_at"], "2026-04-02T03:00:00Z")

        loaded = account_connect.get_ai_reply_settings()
        self.assertEqual(loaded["openrouter_key"], "sk-or-v1-global")
        self.assertEqual(loaded["primary_model"], "openrouter/free")
        self.assertEqual(
            loaded["fallback_models"],
            ["stepfun/step-3.5-flash:free", "minimax/minimax-m2.5:free"],
        )

    def test_record_youtube_quota_usage_accumulates_breakdown(self) -> None:
        first = account_connect.record_youtube_quota_usage("load_inbox", 1)
        second = account_connect.record_youtube_quota_usage("post_reply", 50)

        self.assertEqual(first["estimated_units_used"], 1)
        self.assertEqual(second["estimated_units_used"], 51)
        self.assertEqual(second["breakdown"]["load_inbox"], 1)
        self.assertEqual(second["breakdown"]["post_reply"], 50)
        self.assertEqual(second["daily_limit"], 10_000)
        self.assertTrue(second["date_pt"])

    def test_quota_snapshot_resets_when_date_changes(self) -> None:
        saved = account_connect.save_youtube_quota_snapshot({
            "date_pt": "2000-01-01",
            "estimated_units_used": 999,
            "breakdown": {"load_inbox": 999},
            "updated_at": "2000-01-01T00:00:00Z",
        })

        self.assertEqual(saved["estimated_units_used"], 0)
        self.assertEqual(saved["breakdown"], {})
        self.assertNotEqual(saved["date_pt"], "2000-01-01")
