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
        policy = SequencePolicy(
            [
                decision(4, 4, "healthy", samples=1),
                decision(4, 2, "high", samples=2),
                decision(4, 2, "high", samples=3),
                decision(4, 3, "degraded", samples=4),
                decision(4, 4, "healthy", samples=5),
            ]
        )
        observed = AuditedShadowPolicy(policy, audit)

        for _ in range(5):
            observed.decide(4)

        self.assertEqual([outcome for _, outcome, _ in audit.events], ["started", "changed", "resolved"])
        self.assertTrue(all(event == "adaptive_worker_shadow_divergence" for event, _, _ in audit.events))
        self.assertEqual(audit.events[0][2]["shadow_worker_delta"], -2)
        self.assertEqual(audit.events[1][2]["shadow_worker_delta"], -1)
        self.assertEqual(audit.events[2][2]["shadow_worker_delta"], 0)
        self.assertEqual(audit.events[2][2]["previous_delta"], -1)

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
