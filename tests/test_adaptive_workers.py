"""Tests for adaptive worker limit stabilization and recovery."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from adaptive_workers import StabilizedWorkerLimit, WorkerLimitTransition
from resource_monitor import HostResourcePolicy


class StubPolicy:
    """Deterministic stub policy for testing stabilization behavior."""

    def __init__(self, allowed: int, pressure: str = "nominal", reason: str = "test", health_score: int = 100):
        self.allowed = allowed
        self.pressure = pressure
        self.reason = reason
        self.health_score = health_score
        self.decide_calls = 0

    def decide(self, configured_workers: int):
        self.decide_calls += 1
        from resource_monitor import ResourceSnapshot, WorkerDecision

        snapshot = ResourceSnapshot(
            total_memory_mib=1024,
            available_memory_mib=512,
            swap_total_mib=0,
            swap_free_mib=0,
            cpu_count=2,
            load1=0.5,
            disk_free_percent=50.0,
            memory_psi_avg10=None,
        )
        return WorkerDecision(
            allowed_workers=self.allowed,
            configured_workers=configured_workers,
            pressure=self.pressure,
            reason=self.reason,
            snapshot=snapshot,
            health_score=self.health_score,
        )


class StabilizedWorkerLimitTests(unittest.TestCase):
    def test_starts_at_target(self) -> None:
        policy = StubPolicy(allowed=3)
        limit = StabilizedWorkerLimit(configured_workers=5, policy=policy, recovery_seconds=1.0)
        self.assertEqual(limit(), 3)

    def test_fast_decrease(self) -> None:
        policy = StubPolicy(allowed=3)
        limit = StabilizedWorkerLimit(configured_workers=5, policy=policy, recovery_seconds=100.0)
        limit()  # start at 3
        policy.allowed = 1
        self.assertEqual(limit(), 1)

    def test_slow_increase(self) -> None:
        policy = StubPolicy(allowed=5)
        limit = StabilizedWorkerLimit(configured_workers=5, policy=policy, recovery_seconds=100.0)
        limit()  # start at 5 (already max)
        self.assertEqual(limit(), 5)

    def test_slow_increase_after_decrease(self) -> None:
        """Recovery is gradual: one worker per recovery interval, not instant."""
        policy = StubPolicy(allowed=5)
        now = [0.0]

        def clock() -> float:
            return now[0]

        limit = StabilizedWorkerLimit(
            configured_workers=5,
            policy=policy,
            recovery_seconds=100.0,
            clock=clock,
        )
        result = limit()
        if hasattr(result, '__await__'):
            import asyncio
            result = asyncio.run(result)
        self.assertEqual(result, 5)

        policy.allowed = 1
        result = limit()
        if hasattr(result, '__await__'):
            import asyncio
            result = asyncio.run(result)
        self.assertEqual(result, 1)

        now[0] += 100.0
        policy.allowed = 5
        result = limit()
        if hasattr(result, '__await__'):
            import asyncio
            result = asyncio.run(result)
        self.assertEqual(result, 2)

        now[0] += 100.0
        policy.allowed = 5
        result = limit()
        if hasattr(result, '__await__'):
            import asyncio
            result = asyncio.run(result)
        self.assertEqual(result, 3)

    def test_transition_audit_callback(self) -> None:
        policy = StubPolicy(allowed=3)
        transitions: list[WorkerLimitTransition] = []
        limit = StabilizedWorkerLimit(
            configured_workers=5,
            policy=policy,
            recovery_seconds=100.0,
            on_transition=transitions.append,
        )
        limit()  # start at 3
        policy.allowed = 1
        limit()  # decrease to 1
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].direction, "decreased")
        self.assertEqual(transitions[0].previous_workers, 3)
        self.assertEqual(transitions[0].stable_workers, 1)

    def test_clamps_to_configured_ceiling(self) -> None:
        policy = StubPolicy(allowed=10)
        limit = StabilizedWorkerLimit(configured_workers=3, policy=policy, recovery_seconds=1.0)
        self.assertEqual(limit(), 3)


class HostResourcePolicySmokeTests(unittest.TestCase):
    def test_decide_returns_decision(self) -> None:
        policy = HostResourcePolicy()
        decision = policy.decide(2)
        self.assertIn(decision.allowed_workers, (1, 2))
        self.assertIn(decision.pressure, {"normal", "high", "critical"})
        self.assertIsInstance(decision.health_score, int)
        self.assertGreaterEqual(decision.health_score, 0)
        self.assertLessEqual(decision.health_score, 100)


if __name__ == "__main__":
    unittest.main()
