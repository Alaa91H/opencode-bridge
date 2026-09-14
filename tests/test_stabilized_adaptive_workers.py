from __future__ import annotations

import unittest
from types import SimpleNamespace

from adaptive_workers import StabilizedWorkerLimit


class FakePolicy:
    def __init__(self, allowed_workers: int) -> None:
        self.allowed_workers = allowed_workers

    def decide(self, _configured_workers: int):
        return SimpleNamespace(allowed_workers=self.allowed_workers)


class StabilizedWorkerLimitTests(unittest.TestCase):
    def test_pressure_reduces_immediately_but_recovery_is_gradual(self) -> None:
        now = {"value": 0.0}
        policy = FakePolicy(4)
        limiter = StabilizedWorkerLimit(4, policy, recovery_seconds=30, clock=lambda: now["value"])

        self.assertEqual(limiter(), 4)

        policy.allowed_workers = 1
        now["value"] = 1.0
        self.assertEqual(limiter(), 1)

        policy.allowed_workers = 4
        now["value"] = 20.0
        self.assertEqual(limiter(), 1)

        now["value"] = 31.0
        self.assertEqual(limiter(), 2)

        now["value"] = 60.0
        self.assertEqual(limiter(), 2)

        now["value"] = 61.0
        self.assertEqual(limiter(), 3)

        now["value"] = 91.0
        self.assertEqual(limiter(), 4)

    def test_returning_pressure_resets_recovery_window(self) -> None:
        now = {"value": 0.0}
        policy = FakePolicy(1)
        limiter = StabilizedWorkerLimit(4, policy, recovery_seconds=10, clock=lambda: now["value"])

        self.assertEqual(limiter(), 1)
        policy.allowed_workers = 4
        now["value"] = 9.0
        self.assertEqual(limiter(), 1)

        policy.allowed_workers = 1
        now["value"] = 10.0
        self.assertEqual(limiter(), 1)

        policy.allowed_workers = 4
        now["value"] = 19.0
        self.assertEqual(limiter(), 1)
        now["value"] = 20.0
        self.assertEqual(limiter(), 2)


if __name__ == "__main__":
    unittest.main()
