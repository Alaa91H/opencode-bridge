"""Robustness tests for the daily-task completion decision."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from bridge.domain.tasks.daily_continuation import (
    COMPLETION_SENTINEL,
    continuation_delay_seconds,
    is_task_complete,
)


class CompletionDecisionTests(unittest.TestCase):
    def test_exact_trailing_sentinel_completes(self) -> None:
        self.assertTrue(is_task_complete("تم كل شيء\n[[BRIDGE_TASK_COMPLETE]]\n"))

    def test_sentinel_with_short_trailing_note_completes(self) -> None:
        self.assertTrue(is_task_complete("[[BRIDGE_TASK_COMPLETE]] — تم"))
        self.assertTrue(is_task_complete("العمل جاهز [[BRIDGE_TASK_COMPLETE]]"))

    def test_mere_mention_of_the_marker_does_not_complete(self) -> None:
        self.assertFalse(is_task_complete(
            "Example: [[BRIDGE_TASK_COMPLETE]] is the marker."))
        self.assertFalse(is_task_complete("Still working."))

    def test_explicit_done_phrase_completes_without_sentinel(self) -> None:
        self.assertTrue(is_task_complete("تقرير اليوم\nاكتملت المهمة"))
        self.assertTrue(is_task_complete("All checks passed.\nTask completed"))

    def test_empty_or_blank_text_never_completes(self) -> None:
        self.assertFalse(is_task_complete(""))
        self.assertFalse(is_task_complete("   \n  "))


class HedgeTests(unittest.TestCase):
    """A hedged last line must never end a multi-day job."""

    def test_english_negation_does_not_complete(self) -> None:
        self.assertFalse(is_task_complete("I am not sure the task is complete"))
        self.assertFalse(is_task_complete("Report\nTask complete? not yet"))
        self.assertFalse(is_task_complete("Work in progress\njob completed? no"))

    def test_arabic_negation_does_not_complete(self) -> None:
        self.assertFalse(is_task_complete("تقرير\nالمهمة ليست مكتملة بعد"))
        self.assertFalse(is_task_complete("تقرير\nلم تكتمل المهمة"))
        self.assertFalse(is_task_complete("تقرير\nاكتملت المهمة جزئيًا"))

    def test_question_after_the_sentinel_is_a_question_not_a_claim(self) -> None:
        self.assertFalse(is_task_complete(f"{COMPLETION_SENTINEL}؟"))
        self.assertFalse(is_task_complete(f"{COMPLETION_SENTINEL}?"))
        self.assertFalse(is_task_complete(f"هل انتهى العمل {COMPLETION_SENTINEL}؟"))

    def test_long_tail_after_a_done_phrase_is_a_mention(self) -> None:
        self.assertFalse(is_task_complete(
            "the task is complete in principle, but the tests fail"))
        self.assertFalse(is_task_complete("summary line\nall done, but the client rejected it"))


class ShortSignOffTests(unittest.TestCase):
    def test_a_done_phrase_with_a_short_sign_off_completes(self) -> None:
        self.assertTrue(is_task_complete("اكتملت المهمة — تقرير اليوم مرفق"))
        self.assertTrue(is_task_complete("All done: report attached."))

    def test_the_sentinel_is_still_the_strongest_signal(self) -> None:
        report = "\n".join(f"سطر {index}" for index in range(50))
        self.assertFalse(is_task_complete(report))
        self.assertTrue(is_task_complete(f"{report}\n{COMPLETION_SENTINEL}"))
        # A marker in the middle of the report is never a completion.
        self.assertFalse(is_task_complete(f"{COMPLETION_SENTINEL}\n{report}"))


class ContinuationDelayTests(unittest.TestCase):
    def test_delay_targets_the_next_utc_noon(self) -> None:
        now = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
        self.assertEqual(
            continuation_delay_seconds(now),
            (datetime(2026, 10, 17, 12, 0, tzinfo=UTC) - now).total_seconds(),
        )

    def test_delay_is_never_zero_or_negative(self) -> None:
        noon = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        self.assertGreaterEqual(continuation_delay_seconds(noon), 1.0)
        self.assertGreater(continuation_delay_seconds(noon + timedelta(seconds=1)), 1.0)


if __name__ == "__main__":
    unittest.main()
