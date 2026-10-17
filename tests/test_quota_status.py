"""Quota-reset visibility lines for `/limits`."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bridge.telegram.commands.config import quota_status_lines


class QuotaStatusLinesTests(unittest.TestCase):
    def test_daily_reset_countdown_is_always_shown(self) -> None:
        now = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
        (line,) = quota_status_lines(now=now)
        self.assertIn("12:00 UTC", line)
        self.assertIn("بعد 2h 0m", line)

    def test_active_pause_adds_exhaustion_state(self) -> None:
        now = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
        lines = quota_status_lines(
            now=now, paused_until=now + timedelta(hours=2))
        self.assertEqual(len(lines), 2)
        self.assertIn("مستنفدة", lines[1])
        self.assertIn("12:00 UTC", lines[1])

    def test_expired_pause_is_hidden(self) -> None:
        now = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)
        lines = quota_status_lines(
            now=now, paused_until=now - timedelta(minutes=5))
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
