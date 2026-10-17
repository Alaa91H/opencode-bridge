"""`/limits` exposes download caps and the pending-updates flag."""

from __future__ import annotations

import unittest

from bridge.config.settings import BridgeSettings
from bridge.telegram.rendering.config import render_limits


class LimitsVisibilityTests(unittest.TestCase):
    def test_download_caps_and_drop_flag_are_listed(self) -> None:
        settings = BridgeSettings.load(env={})
        limits = settings.limits_dict()
        self.assertEqual(limits["download_max_file_bytes"], 8 * 1024 * 1024 * 1024)
        self.assertEqual(limits["download_max_total_bytes"], 40 * 1024 * 1024 * 1024)
        self.assertEqual(limits["download_direct_send_limit"], 50 * 1024 * 1024)
        self.assertEqual(limits["download_ttl_hours"], 24)
        self.assertFalse(limits["drop_pending_updates"])
        text = render_limits(limits)
        for label in ("أقصى حجم ملف منزّل", "إجمالي مساحة التنزيلات",
                      "حد الإرسال المباشر", "مهلة احتفاظ التنزيلات",
                      "تجاهل الرسائل المتراكمة"):
            self.assertIn(label, text)

    def test_drop_flag_is_configurable(self) -> None:
        settings = BridgeSettings.load(env={"TELEGRAM_DROP_PENDING_UPDATES": "1"})
        self.assertTrue(settings.limits_dict()["drop_pending_updates"])


if __name__ == "__main__":
    unittest.main()
