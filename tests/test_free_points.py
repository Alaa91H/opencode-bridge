from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from free_points import FreePointsTracker, format_free_points_header

UTC = timezone.utc


class FreePointsTrackerTests(unittest.TestCase):
    def test_records_daily_points_and_resets_by_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = FreePointsTracker(Path(tmp) / "points.db", daily_limit=200, timezone_name="Europe/Berlin")
            day_one = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
            day_two = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)

            snapshot = tracker.record(3, now=day_one)
            self.assertEqual(snapshot.used, 3)
            self.assertEqual(snapshot.remaining, 197)

            snapshot = tracker.record(2, now=day_one)
            self.assertEqual(snapshot.used, 5)
            self.assertEqual(snapshot.remaining, 195)

            next_day = tracker.snapshot(now=day_two)
            self.assertEqual(next_day.used, 0)
            self.assertEqual(next_day.remaining, 200)

    def test_exhaustion_forces_remaining_to_zero_without_erasing_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = FreePointsTracker(Path(tmp) / "points.db", daily_limit=200, timezone_name="UTC")
            now = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
            tracker.record(11, now=now)
            snapshot = tracker.mark_exhausted(now=now)

            self.assertTrue(snapshot.exhausted)
            self.assertEqual(snapshot.used, 11)
            self.assertEqual(snapshot.remaining, 0)

    def test_header_is_exactly_two_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = FreePointsTracker(Path(tmp) / "points.db", daily_limit=200, timezone_name="UTC")
            snapshot = tracker.record(3, now=datetime(2026, 9, 21, 10, 0, tzinfo=UTC))
            header = format_free_points_header(snapshot, 3)

            self.assertEqual(
                header,
                "النقاط المجانية المتبقية اليوم (تقديري): 197/200\n"
                "استهلاك هذا الأمر: 3 نقاط",
            )
            self.assertEqual(len(header.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
