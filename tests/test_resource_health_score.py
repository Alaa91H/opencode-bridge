from __future__ import annotations

import unittest

from resource_monitor import HostResourcePolicy, ResourceSnapshot, format_decision


class ResourceHealthScoreTests(unittest.TestCase):
    def sample(
        self,
        *,
        total=4096,
        available=3072,
        swap_total=1024,
        swap_free=1024,
        cpus=4,
        load1=0.5,
        disk=50.0,
        psi=0.0,
    ) -> ResourceSnapshot:
        return ResourceSnapshot(total, available, swap_total, swap_free, cpus, load1, disk, psi)

    def test_healthy_host_scores_near_full_health(self) -> None:
        self.assertEqual(HostResourcePolicy.health_score(self.sample()), 100)

    def test_multiple_moderate_pressures_accumulate(self) -> None:
        snapshot = self.sample(
            available=1200,
            swap_free=500,
            load1=3.5,
            disk=15.0,
            psi=1.0,
        )
        score = HostResourcePolicy.health_score(snapshot)
        self.assertGreaterEqual(score, 0)
        self.assertLess(score, 70)

    def test_critical_combined_pressure_clamps_at_zero(self) -> None:
        snapshot = self.sample(
            available=200,
            swap_free=50,
            load1=9.0,
            disk=3.0,
            psi=15.0,
        )
        self.assertEqual(HostResourcePolicy.health_score(snapshot), 0)

    def test_tiny_swap_does_not_reduce_health(self) -> None:
        healthy = self.sample(swap_total=0, swap_free=0)
        tiny_exhausted = self.sample(swap_total=128, swap_free=0)
        self.assertEqual(
            HostResourcePolicy.health_score(tiny_exhausted),
            HostResourcePolicy.health_score(healthy),
        )

    def test_decision_and_diagnostics_expose_health_score(self) -> None:
        class FixedPolicy(HostResourcePolicy):
            def snapshot(self, force: bool = False) -> ResourceSnapshot:
                return self_value

        self_value = self.sample(available=600)
        decision = FixedPolicy().decide(4)
        self.assertLess(decision.health_score, 100)
        self.assertIn(f"Health score: {decision.health_score}/100", format_decision(decision))


if __name__ == "__main__":
    unittest.main()
