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

    def test_health_levels_have_explicit_boundaries(self) -> None:
        self.assertEqual(HostResourcePolicy.health_level(100), "healthy")
        self.assertEqual(HostResourcePolicy.health_level(80), "healthy")
        self.assertEqual(HostResourcePolicy.health_level(79), "degraded")
        self.assertEqual(HostResourcePolicy.health_level(60), "degraded")
        self.assertEqual(HostResourcePolicy.health_level(59), "high")
        self.assertEqual(HostResourcePolicy.health_level(35), "high")
        self.assertEqual(HostResourcePolicy.health_level(34), "critical")

    def test_shadow_worker_caps_are_observational_and_bounded(self) -> None:
        self.assertEqual(HostResourcePolicy.shadow_worker_limit(8, "healthy"), 8)
        self.assertEqual(HostResourcePolicy.shadow_worker_limit(8, "degraded"), 3)
        self.assertEqual(HostResourcePolicy.shadow_worker_limit(8, "high"), 2)
        self.assertEqual(HostResourcePolicy.shadow_worker_limit(8, "critical"), 1)
        self.assertEqual(HostResourcePolicy.shadow_worker_limit(2, "degraded"), 2)

    def test_shadow_health_recovers_with_hysteresis_one_level_at_a_time(self) -> None:
        stabilize = HostResourcePolicy.stabilize_shadow_health_level
        self.assertEqual(stabilize(34, "healthy"), "critical")
        self.assertEqual(stabilize(39, "critical"), "critical")
        self.assertEqual(stabilize(40, "critical"), "high")
        self.assertEqual(stabilize(64, "high"), "high")
        self.assertEqual(stabilize(65, "high"), "degraded")
        self.assertEqual(stabilize(84, "degraded"), "degraded")
        self.assertEqual(stabilize(85, "degraded"), "healthy")

    def test_decision_exposes_shadow_comparison_without_changing_admission(self) -> None:
        snapshots = [
            self.sample(available=200, psi=15.0),
            self.sample(),
        ]

        class FixedPolicy(HostResourcePolicy):
            def snapshot(self, force: bool = False) -> ResourceSnapshot:
                return snapshots.pop(0)

        policy = FixedPolicy()
        critical = policy.decide(4)
        self.assertEqual(critical.allowed_workers, 1)
        self.assertEqual(critical.shadow_health_level, "critical")
        self.assertEqual(critical.shadow_allowed_workers, 1)

        recovered_sample = policy.decide(4)
        self.assertEqual(recovered_sample.allowed_workers, 4)
        self.assertEqual(recovered_sample.health_level, "healthy")
        self.assertEqual(recovered_sample.shadow_health_level, "high")
        self.assertEqual(recovered_sample.shadow_allowed_workers, 2)

    def test_decision_and_diagnostics_expose_health_score(self) -> None:
        class FixedPolicy(HostResourcePolicy):
            def snapshot(self, force: bool = False) -> ResourceSnapshot:
                return self_value

        self_value = self.sample(available=600)
        decision = FixedPolicy().decide(4)
        self.assertLess(decision.health_score, 100)
        diagnostics = format_decision(decision)
        self.assertIn(f"Health score: {decision.health_score}/100", diagnostics)
        self.assertIn(f"Shadow health: {decision.shadow_health_level}", diagnostics)


if __name__ == "__main__":
    unittest.main()
