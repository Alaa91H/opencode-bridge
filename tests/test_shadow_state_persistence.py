from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

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
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.events: list[tuple[str, str, dict]] = []

    def write(self, event: str, outcome: str, *, actor_id=None, details=None) -> None:
        self.events.append((event, outcome, details or {}))


def decision(production: int = 4, shadow: int = 4) -> WorkerDecision:
    delta = shadow - production
    return WorkerDecision(
        allowed_workers=production,
        configured_workers=4,
        pressure="normal",
        reason="test",
        snapshot=SNAPSHOT,
        health_score=90,
        health_level="healthy",
        shadow_health_level="healthy",
        shadow_allowed_workers=shadow,
        shadow_worker_delta=delta,
    )


def make_policy(
    state_path: Path,
    monotonic: list[float],
    wall: list[float],
    audit: FakeAudit | None = None,
    *,
    max_state_age_seconds: float = 900.0,
) -> AuditedShadowPolicy:
    return AuditedShadowPolicy(
        SequencePolicy([decision()] * 8),
        audit or FakeAudit(),
        evaluation_window=5,
        promotion_min_samples=5,
        promotion_min_agreement_percent=100.0,
        promotion_max_mean_abs_delta=0.0,
        promotion_max_abs_delta=0,
        promotion_max_aggressive_percent=0.0,
        promotion_min_stable_seconds=100.0,
        clock=lambda: monotonic[0],
        wall_clock=lambda: wall[0],
        state_path=state_path,
        persist_interval_seconds=0.0,
        max_state_age_seconds=max_state_age_seconds,
    )


class ShadowStatePersistenceTests(unittest.TestCase):
    def test_restores_recent_stable_evidence_without_counting_downtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "shadow-readiness.json"
            mono = [100.0]
            wall = [1000.0]
            first = make_policy(state_path, mono, wall)
            for _ in range(5):
                first.decide(4)
            mono[0] = 140.0
            wall[0] = 1040.0
            first.decide(4)
            self.assertEqual(first.readiness().stable_for_seconds, 40.0)
            self.assertTrue(state_path.exists())
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)

            restart_mono = [10.0]
            restart_wall = [1100.0]  # 60 seconds of downtime must not count as stable evidence.
            audit = FakeAudit()
            restored = make_policy(state_path, restart_mono, restart_wall, audit)
            status = restored.readiness()
            self.assertEqual(status.samples, 5)
            self.assertEqual(status.stable_for_seconds, 40.0)
            self.assertFalse(status.promotion_ready)
            self.assertIn(("adaptive_worker_shadow_state", "restored"), [(e, o) for e, o, _ in audit.events])

            restart_mono[0] = 70.0
            self.assertTrue(restored.readiness().promotion_ready)

    def test_rejects_stale_state_instead_of_trusting_old_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "shadow-readiness.json"
            state_path.write_text(
                json.dumps({
                    "version": 1,
                    "saved_at_unix": 1000.0,
                    "deltas": [0, 0, 0, 0, 0],
                    "stable_elapsed_seconds": 100.0,
                }),
                encoding="utf-8",
            )
            audit = FakeAudit()
            restored = make_policy(state_path, [50.0], [2000.0], audit, max_state_age_seconds=100.0)
            self.assertEqual(restored.readiness().samples, 0)
            ignored = [details for event, outcome, details in audit.events if event == "adaptive_worker_shadow_state" and outcome == "ignored"]
            self.assertTrue(ignored)

    def test_derives_state_path_from_audit_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            audit = FakeAudit(runtime / "audit.jsonl")
            observed = AuditedShadowPolicy(
                SequencePolicy([decision()] * 5),
                audit,
                evaluation_window=5,
                promotion_min_samples=5,
                promotion_min_stable_seconds=0.0,
                persist_interval_seconds=0.0,
            )
            observed.decide(4)
            self.assertTrue((runtime / "shadow-readiness.json").exists())


if __name__ == "__main__":
    unittest.main()
