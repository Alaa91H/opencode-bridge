"""Tests for bridge.telegram.helpers (pure utilities extracted from bot.py)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from bridge.telegram.helpers import parse_utc_datetime, task_status_text, utc_now


class TaskStatusTextTests(unittest.TestCase):
    def test_known_statuses(self) -> None:
        cases = {
            "queued": "بانتظار التنفيذ",
            "scheduled": "مجدولة",
            "running": "قيد التنفيذ",
            "completed": "مكتملة",
            "failed": "فشلت",
            "cancelled": "ملغاة",
        }
        for status, expected in cases.items():
            task = type("Task", (), {"status": status})()
            self.assertEqual(task_status_text(task), expected)

    def test_unknown_status_passthrough(self) -> None:
        task = type("Task", (), {"status": "weird-status"})()
        self.assertEqual(task_status_text(task), "weird-status")

    def test_missing_status_attribute(self) -> None:
        task = object()
        self.assertEqual(task_status_text(task), "")


class ParseUtcDatetimeTests(unittest.TestCase):
    def test_valid_iso_datetime(self) -> None:
        dt = parse_utc_datetime("2026-10-21 14:30")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)
        self.assertEqual(dt.day, 21)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 30)

    def test_invalid_datetime_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_utc_datetime("not-a-date")


class UtcNowTests(unittest.TestCase):
    def test_returns_aware_utc_datetime(self) -> None:
        dt = utc_now()
        self.assertIsNotNone(dt.tzinfo)
        self.assertEqual(dt.tzinfo.utcoffset(dt).total_seconds(), 0)


if __name__ == "__main__":
    unittest.main()
