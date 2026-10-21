import unittest
from pathlib import Path
from types import SimpleNamespace

from bridge.telegram.execution import TelegramExecutionDelivery


class PointsIndicatorRemovalTests(unittest.TestCase):
    def test_production_sources_have_no_points_indicator_contract(self):
        paths = [Path("bot.py"), Path("opencode_client.py"), Path(".env.example"), Path("README.md")]
        paths += list(Path("bridge").rglob("*.py"))
        paths += list(Path("maintenance").glob("*.py"))
        forbidden = (
            "free_points",
            "_bridge_usage_points",
            "OPENCODE_FREE_DAILY_POINTS",
            "TELEGRAM_DAILY_TASK_COUNTER_TIMEZONE",
            "النقاط المجانية المتبقية",
            "استهلاك هذا الأمر",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{token!r} remains in {path}")

    def test_final_telegram_text_contains_only_result_body(self):
        delivery = TelegramExecutionDelivery(
            SimpleNamespace(), None, None, {},
            max_message_length=4096,
            error_message=str,
        )
        self.assertEqual(delivery.final_text("النتيجة"), "النتيجة")

    def test_tracker_module_is_removed(self):
        self.assertFalse(Path("free_points.py").exists())
