from __future__ import annotations

import unittest

from resource_monitor import ResourceSnapshot, WorkerDecision
from shadow_policy_audit import AuditedShadowPolicy


SNAPSHOT = ResourceSnapshot(
    total_memory_mib=4096,
    available_memory_mib=3072,
    swap_total_mib=1024,
    swap_free_mib=1024,
    cpu_count=4,
    load1=0.2,
    disk_free_percent=80.0,
    memory_psi_avg10=0.0,
)


class SequencePolicy:
    def __init__(self, decisions: list[WorkerDecision]) -> None:
        self.decisions = decisions
        self.index = 0

    def decide(self, configured_workers: int) -> WorkerDecision:
        decision = self.decisions[min(self.index, len(self.decisions) - 1)]
        self.index += 1
        return decision


class FakeAudit:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple[str, str, dict]] = []

    def write(self, event: str, outcome: str, *, actor_id=None, details=None) -> None:
        if self.fail:
            raise OSError("audit unavailable")
        self.events.append((event, outcome, details or {}))


def decision(production: int, shadow: int, level: str, *, samples: int) -> WorkerDecision:
    delta = shadow - production
    return WorkerDecision(
        allowed_workers=production,
        configured_workers=4,
        pressure="normal",
        reason="test",
        snapshot=SNAPSHOT,
        health_score=70,
        health_level="degraded",
        shadow_health_level=level,
        shadow_allowed_workers=shadow,
        shadow_worker_delta=delta,
        comparison_samples=samples,
        comparison_agreements=max(0, samples - 1),
        comparison_disagreements=1 if delta else 0,
        comparison_max_abs_delta=abs(delta),
    )


class ShadowPolicyAuditTests(unittest.TestCase):
    def test_audits_start_change_and_resolution_without_sample_spam(self) -> None:
        audit = FakeAudit()
        policy = SequencePolicy([
            decision(4, 4, "healthy", samples=1),
            decision(4, 2, "high", samples=2),
            decision(4, 2, "high", samples=3),
            decision(4, 3, "degraded", samples=4),
            decision(4, 4, "healthy", samples=5),
        ])
        observed = AuditedShadowPolicy(policy, audit)
        for _ in range(5):
            observed.decide(4)
        self.assertEqual([outcome for _, outcome, _ in audit.events], ["started", "changed", "resolved"])

    def test_readiness_requires_sustained_observation_time(self) -> None:
        now = [100.0]
        audit = FakeAudit()
        observed = AuditedShadowPolicy(
            SequencePolicy([decision(4, 4, "healthy", samples=i) for i in range(1, 7)]),
            audit,
            evaluation_window=5,
            promotion_min_samples=5,
            promotion_min_agreement_percent=100.0,
            promotion_max_mean_abs_delta=0.0,
            promotion_max_abs_delta=0,
            promotion_max_aggressive_percent=0.0,
            promotion_min_observation_seconds=3600.0,
            clock=lambda: now[0],
        )
        for _ in range(5):
            observed.decide(4)
        early = observed.readiness()
        self.assertFalse(early.promotion_ready)
        self.assertIn("observation time", early.reason)
        self.assertEqual(early.observed_for_seconds, 0.0)
        now[0] += 3600.0
        observed.decide(4)
        ready = observed.readiness()
        self.assertTrue(ready.promotion_ready)
        self.assertEqual(ready.observed_for_seconds, 3600.0)
        readiness_events = [item for item in audit.events if item[0] == "adaptive_worker_shadow_readiness"]
        self.assertEqual([outcome for _, outcome, _ in readiness_events], ["not_ready", "ready"])
        self.assertEqual(readiness_events[-1][2]["required_observation_seconds"], 3600.0)
        self.assertTrue(readiness_events[-1][2]["advisory_only"])

    def test_readiness_distinguishes_conservative_from_aggressive_shadow_bias(self) -> None:
        conservative = AuditedShadowPolicy(
            SequencePolicy([decision(4, 3, "degraded", samples=1)] * 5),
            FakeAudit(),
            evaluation_window=5,
            promotion_min_samples=5,
            promotion_min_observation_seconds=0,
        )
        for _ in range(5):
            conservative.decide(4)
        status = conservative.readiness()
        self.assertEqual(status.aggressive_samples, 0)
        self.assertEqual(status.conservative_samples, 5)
        self.assertFalse(status.promotion_ready)

    def test_audit_failure_never_changes_production_decision(self) -> None:
        policy = SequencePolicy([decision(4, 1, "critical", samples=1)])
        observed = AuditedShadowPolicy(policy, FakeAudit(fail=True))
        result = observed.decide(4)
        self.assertEqual(result.allowed_workers, 4)
        self.assertEqual(result.shadow_allowed_workers, 1)

    def test_delegates_non_decision_policy_attributes(self) -> None:
        policy = SequencePolicy([decision(4, 4, "healthy", samples=1)])
        policy.marker = "delegated"  # type: ignore[attr-defined]
        observed = AuditedShadowPolicy(policy, FakeAudit())
        self.assertEqual(observed.marker, "delegated")


if __name__ == "__main__":
    unittest.main()
