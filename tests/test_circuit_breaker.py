"""Tests for circuit breaker resilience."""

from __future__ import annotations

import unittest

from opencode_client import CircuitBreaker


class StubClock:
    """Deterministic clock for testing recovery timing."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


class CircuitBreakerTests(unittest.TestCase):
    def test_starts_closed(self) -> None:
        cb = CircuitBreaker()
        self.assertTrue(cb.can_proceed())

    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        self.assertTrue(cb.can_proceed())
        cb.record_failure()
        self.assertFalse(cb.can_proceed())

    def test_opens_immediately_at_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        self.assertFalse(cb.can_proceed())

    def test_recovers_after_cooldown(self) -> None:
        now = [0.0]
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=100.0, clock=lambda: now[0])
        cb.record_failure()
        self.assertFalse(cb.can_proceed())
        now[0] += 100.0
        self.assertTrue(cb.can_proceed())  # half-open allows trial

    def test_half_open_closes_on_success(self) -> None:
        now = [0.0]
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=100.0, clock=lambda: now[0])
        cb.record_failure()
        now[0] += 100.0
        self.assertTrue(cb.can_proceed())  # half-open
        cb.record_success()
        self.assertTrue(cb.can_proceed())  # closed again

    def test_record_outcome(self) -> None:
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_outcome(True)
        self.assertTrue(cb.can_proceed())
        cb.record_outcome(False)
        self.assertFalse(cb.can_proceed())

    def test_record_outcome_records_multiple_failures(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_outcome(False)
        cb.record_outcome(False)
        self.assertTrue(cb.can_proceed())
        cb.record_outcome(False)
        self.assertFalse(cb.can_proceed())


if __name__ == "__main__":
    unittest.main()
