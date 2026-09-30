import unittest
from datetime import UTC, datetime

from bridge.domain.schedules.engine import (
    MisfirePolicy,
    OverlapPolicy,
    Recurrence,
    due_occurrences,
    overlap_action,
    validate_timezone,
)

UTC = UTC


class SchedulerEngineTests(unittest.TestCase):
    def test_iana_timezone_validation(self):
        self.assertEqual(validate_timezone("Europe/Berlin"), "Europe/Berlin")
        with self.assertRaises(ValueError):
            validate_timezone("Mars/Olympus")

    def test_interval_and_misfire_policies(self):
        recurrence = Recurrence("interval", interval_seconds=60)
        due = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        now = datetime(2026, 1, 1, 0, 3, tzinfo=UTC)
        self.assertEqual(due_occurrences(due, now, recurrence, MisfirePolicy.SKIP), [])
        self.assertEqual(len(due_occurrences(due, now, recurrence, MisfirePolicy.RUN_ONCE)), 1)
        self.assertEqual(len(due_occurrences(due, now, recurrence, MisfirePolicy.COALESCE)), 1)
        self.assertEqual(len(due_occurrences(due, now, recurrence, MisfirePolicy.CATCH_UP)), 4)

    def test_daily_spring_dst_keeps_local_wall_time(self):
        recurrence = Recurrence("daily", timezone_name="Europe/Berlin", hour=9)
        before = datetime(2026, 3, 28, 8, 0, tzinfo=UTC)  # 09:00 CET
        after = recurrence.next_after(before)
        self.assertEqual(after, datetime(2026, 3, 29, 7, 0, tzinfo=UTC))  # 09:00 CEST

    def test_daily_fall_dst_keeps_local_wall_time(self):
        recurrence = Recurrence("daily", timezone_name="Europe/Berlin", hour=9)
        before = datetime(2026, 10, 24, 7, 0, tzinfo=UTC)  # 09:00 CEST
        after = recurrence.next_after(before)
        self.assertEqual(after, datetime(2026, 10, 25, 8, 0, tzinfo=UTC))  # 09:00 CET

    def test_weekly_monthly_weekdays_and_cron(self):
        base = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)  # Monday
        weekly = Recurrence("weekly", timezone_name="UTC", hour=8, weekdays=(2,))
        self.assertEqual(weekly.next_after(base).weekday(), 2)
        weekdays = Recurrence("weekdays", timezone_name="UTC", hour=8, weekdays=(0,1,2,3,4))
        self.assertEqual(weekdays.next_after(datetime(2026, 10, 2, 8, tzinfo=UTC)).weekday(), 0)
        monthly = Recurrence("monthly", timezone_name="UTC", hour=8, day_of_month=15)
        self.assertEqual(monthly.next_after(base).day, 15)
        cron = Recurrence("cron", cron="30 8 * * 1", timezone_name="UTC")
        nxt = cron.next_after(base)
        self.assertEqual((nxt.hour, nxt.minute, (nxt.weekday()+1)%7), (8,30,1))

    def test_overlap_policy_decisions(self):
        self.assertEqual(overlap_action(OverlapPolicy.FORBID, 1), "skip")
        self.assertEqual(overlap_action(OverlapPolicy.ALLOW, 1), "enqueue")
        self.assertEqual(overlap_action(OverlapPolicy.REPLACE, 1), "replace")
        self.assertEqual(overlap_action(OverlapPolicy.QUEUE, 1), "queue")
        self.assertEqual(overlap_action(OverlapPolicy.FORBID, None), "enqueue")


if __name__ == "__main__":
    unittest.main()
