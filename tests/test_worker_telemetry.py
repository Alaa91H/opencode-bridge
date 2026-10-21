from __future__ import annotations

import unittest
from types import SimpleNamespace

from adaptive_workers import StabilizedWorkerLimit
from resource_monitor import ResourceSnapshot, WorkerDecision


class FakePolicy:
    def __init__(self) -> None:
        self.allowed = 1

    def decide(self, configured_workers: int) -> WorkerDecision:
        snapshot = ResourceSnapshot(
            total_memory_mib=4096,
            available_memory_mib=2048,
            swap_total_mib=1024,
            swap_free_mib=1024,
            cpu_count=4,
            load1=0.2,
            disk_free_percent=80.0,
            memory_psi_avg10=0.0,
        )
        pressure = "high" if self.allowed < configured_workers else "normal"
        reason = "memory pressure" if pressure == "high" else "resources healthy"
        return WorkerDecision(self.allowed, configured_workers, pressure, reason, snapshot)


class ReadinessPolicy(FakePolicy):
    def readiness(self):
        return SimpleNamespace(
            promotion_ready=False,
            reason="observation time below 21600s",
            samples=30,
            agreement_percent=96.7,
            observed_for_seconds=1800.0,
            required_observation_seconds=21600.0,
        )


class WorkerTelemetryTests(unittest.TestCase):
    def test_status_exposes_stable_target_and_recovery_window(self) -> None:
        now = [100.0]
        policy = FakePolicy()
        limiter = StabilizedWorkerLimit(4, policy, recovery_seconds=30.0, clock=lambda: now[0])

        self.assertEqual(limiter(), 1)
        policy.allowed = 4
        now[0] = 110.0
        self.assertEqual(limiter(), 1)

        status = limiter.status()
        self.assertEqual(status.stable_workers, 1)
        self.assertEqual(status.raw_target_workers, 4)
        self.assertAlmostEqual(status.recovery_remaining_seconds, 20.0)
        self.assertEqual(status.reason, "resources healthy")

        now[0] = 131.0
        self.assertEqual(limiter(), 2)
        status = limiter.status()
        self.assertEqual(status.stable_workers, 2)
        self.assertEqual(status.raw_target_workers, 4)
        self.assertGreater(status.recovery_remaining_seconds, 0.0)

    def test_status_exposes_advisory_shadow_readiness_without_mutating_limit(self) -> None:
        policy = ReadinessPolicy()
        policy.allowed = 2
        limiter = StabilizedWorkerLimit(4, policy, recovery_seconds=30.0, clock=lambda: 100.0)
        self.assertEqual(limiter(), 2)
        status = limiter.status()
        self.assertEqual(status.stable_workers, 2)
        self.assertFalse(status.shadow_promotion_ready)
        self.assertEqual(status.shadow_readiness_samples, 30)
        self.assertAlmostEqual(status.shadow_agreement_percent, 96.7)
        self.assertEqual(status.shadow_observed_for_seconds, 1800.0)
        self.assertEqual(status.shadow_required_observation_seconds, 21600.0)
        self.assertIn("observation time", status.shadow_readiness_reason)


if __name__ == "__main__":
    unittest.main()
