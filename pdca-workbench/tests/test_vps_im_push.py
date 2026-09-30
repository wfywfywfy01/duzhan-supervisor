# -*- coding: utf-8 -*-
"""app.vps_im_push tests: persistent channel override."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import vps_im_push


class PushChannelOverrideTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_patch = patch(
            "app.vps_im_push.get_settings",
            return_value=type("SettingsStub", (), {"data_dir": Path(self.temp_dir.name)})(),
        )
        self.settings_patch.start()
        self.post = patch("app.vps_im_push.httpx.post")
        self.post_mock = self.post.start()
        self.post_mock.return_value.status_code = 200
        self.post_mock.return_value.json.return_value = {"ok": True}

    def tearDown(self):
        self.post.stop()
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def _write_override(self, value: str) -> None:
        runtime = Path(self.temp_dir.name) / 'runtime'
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / 'push_channel.txt').write_text(value, encoding='utf-8')

    def test_override_file_wins_over_env(self):
        self._write_override('channel-from-file')
        with patch.dict("os.environ", {
            "PDCA_VPS_BOT_APP_ID": "app",
            "PDCA_VPS_BOT_APP_SECRET": "sec",
            "PDCA_VPS_BOT_CHANNEL_ID": "channel-from-env",
        }):
            result = vps_im_push.push_vps_message("hi")
        self.assertTrue(result)
        sent_json = self.post_mock.call_args.kwargs["json"]
        self.assertEqual(sent_json["channel_id"], "channel-from-file")

    def test_business_push_adds_channel_to_idempotency_key(self):
        self._write_override('channel-from-file')
        with patch.dict("os.environ", {
            "PDCA_VPS_BOT_APP_ID": "app",
            "PDCA_VPS_BOT_APP_SECRET": "sec",
        }):
            result = vps_im_push.push_vps_message(
                "hi", idempotency_key="daily_report:2026-09-22"
            )
        self.assertTrue(result)
        sent_json = self.post_mock.call_args.kwargs["json"]
        expected = "daily_report:2026-09-22:channel-from-file"
        self.assertEqual(sent_json["idempotency_key"], expected)
        self.assertEqual(
            self.post_mock.call_args.kwargs["headers"]["Idempotency-Key"], expected
        )

    def test_env_fallback_when_no_override(self):
        with patch.dict("os.environ", {
            "PDCA_VPS_BOT_APP_ID": "app",
            "PDCA_VPS_BOT_APP_SECRET": "sec",
            "PDCA_VPS_BOT_CHANNEL_ID": "channel-from-env",
        }):
            result = vps_im_push.push_vps_message("hi")
        self.assertTrue(result)
        sent_json = self.post_mock.call_args.kwargs["json"]
        self.assertEqual(sent_json["channel_id"], "channel-from-env")

    def test_no_channel_returns_false(self):
        with patch.dict("os.environ", {
            "PDCA_VPS_BOT_APP_ID": "app",
            "PDCA_VPS_BOT_APP_SECRET": "sec",
        }, clear=True):
            self.assertFalse(vps_im_push.push_vps_message("hi"))
        self.post_mock.assert_not_called()

    def _write_alert_override(self, value: str) -> None:
        runtime = Path(self.temp_dir.name) / 'runtime'
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / 'alert_channel.txt').write_text(value, encoding='utf-8')

    def test_alert_override_file_wins(self):
        self._write_alert_override('alert-from-file')
        with patch.dict("os.environ", {
            "PDCA_VPS_BOT_APP_ID": "app",
            "PDCA_VPS_BOT_APP_SECRET": "sec",
            "PDCA_VPS_BOT_CHANNEL_ID": "channel-from-env",
        }):
            result = vps_im_push.push_vps_alert("boom")
        self.assertTrue(result)
        sent_json = self.post_mock.call_args.kwargs["json"]
        self.assertEqual(sent_json["channel_id"], "alert-from-file")

    def test_alert_env_fallback_chain(self):
        with patch.dict("os.environ", {
            "PDCA_VPS_BOT_APP_ID": "app",
            "PDCA_VPS_BOT_APP_SECRET": "sec",
            "PDCA_ALERT_BOT_CHANNEL_ID": "alert-env-channel",
            "PDCA_VPS_BOT_CHANNEL_ID": "default-channel",
        }):
            result = vps_im_push.push_vps_alert("boom")
        self.assertTrue(result)
        sent_json = self.post_mock.call_args.kwargs["json"]
        self.assertEqual(sent_json["channel_id"], "alert-env-channel")

    def test_alert_never_falls_back_to_business_channel(self):
        with patch.dict("os.environ", {
            "PDCA_VPS_BOT_APP_ID": "app",
            "PDCA_VPS_BOT_APP_SECRET": "sec",
            "PDCA_VPS_BOT_CHANNEL_ID": "business-channel",
        }, clear=True):
            result = vps_im_push.push_vps_alert("boom")
        self.assertFalse(result)
        self.post_mock.assert_not_called()

    def test_alert_rejects_same_channel_as_business(self):
        with patch.dict("os.environ", {
            "PDCA_VPS_BOT_APP_ID": "app",
            "PDCA_VPS_BOT_APP_SECRET": "sec",
            "PDCA_ALERT_BOT_CHANNEL_ID": "same-channel",
            "PDCA_VPS_BOT_CHANNEL_ID": "same-channel",
        }, clear=True):
            result = vps_im_push.push_vps_alert("boom")
        self.assertFalse(result)
        self.post_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
