from __future__ import annotations

import unittest

from adaptive_workers import StabilizedWorkerLimit, WorkerLimitTransition
from resource_monitor import ResourceSnapshot, WorkerDecision
from task_service import TaskService


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


class MutablePolicy:
    def __init__(self, allowed: int = 4, pressure: str = "normal", reason: str = "resources healthy") -> None:
        self.allowed = allowed
        self.pressure = pressure
        self.reason = reason

    def decide(self, configured_workers: int) -> WorkerDecision:
        return WorkerDecision(
            allowed_workers=self.allowed,
            configured_workers=configured_workers,
            pressure=self.pressure,
            reason=self.reason,
            snapshot=SNAPSHOT,
        )


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def write(self, event: str, outcome: str, *, actor_id=None, details=None) -> None:
        self.events.append((event, outcome, details or {}))


async def _executor(_task) -> None:
    return None


class WorkerTransitionAuditTests(unittest.TestCase):
    def test_controller_emits_only_real_limit_transitions(self) -> None:
        now = {"value": 0.0}
        policy = MutablePolicy()
        transitions: list[WorkerLimitTransition] = []
        controller = StabilizedWorkerLimit(
            4,
            policy,  # type: ignore[arg-type]
            recovery_seconds=10.0,
            clock=lambda: now["value"],
            on_transition=transitions.append,
        )

        self.assertEqual(controller(), 4)
        self.assertEqual(controller(), 4)
        self.assertEqual(transitions, [])

        now["value"] = 2.0
        policy.allowed = 1
        policy.pressure = "critical"
        policy.reason = "low available memory"
        self.assertEqual(controller(), 1)
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].direction, "decreased")
        self.assertEqual(transitions[0].previous_workers, 4)
        self.assertEqual(transitions[0].stable_workers, 1)
        self.assertEqual(transitions[0].reason, "low available memory")

        policy.allowed = 4
        policy.pressure = "normal"
        policy.reason = "resources healthy"
        now["value"] = 11.0
        self.assertEqual(controller(), 1)
        self.assertEqual(len(transitions), 1)

        now["value"] = 12.0
        self.assertEqual(controller(), 2)
        self.assertEqual(len(transitions), 2)
        self.assertEqual(transitions[1].direction, "increased")
        self.assertEqual(transitions[1].raw_target_workers, 4)
        self.assertEqual(transitions[1].elapsed_since_last_change_seconds, 10.0)

    def test_task_service_writes_structured_audit_event(self) -> None:
        audit = FakeAudit()
        service = TaskService(
            store=None,  # type: ignore[arg-type]
            executor=_executor,
            max_workers=4,
            resource_policy=MutablePolicy(),  # type: ignore[arg-type]
            audit_logger=audit,  # type: ignore[arg-type]
        )
        service._audit_worker_transition(
            WorkerLimitTransition(
                previous_workers=4,
                stable_workers=2,
                raw_target_workers=2,
                direction="decreased",
                pressure="high",
                reason="memory pressure",
                elapsed_since_last_change_seconds=5.5,
            )
        )

        self.assertEqual(len(audit.events), 1)
        event, outcome, details = audit.events[0]
        self.assertEqual(event, "adaptive_worker_limit")
        self.assertEqual(outcome, "decreased")
        self.assertEqual(details["previous_workers"], 4)
        self.assertEqual(details["stable_workers"], 2)
        self.assertEqual(details["reason"], "memory pressure")


if __name__ == "__main__":
    unittest.main()
