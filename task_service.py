"""Production task service with stable live host-pressure-aware concurrency.

This is the **middle layer** of the three-tier task architecture (see
``task_service_v3.py`` for the full picture). It subclasses ``TaskServiceV3``
and adds:

- ``HostResourcePolicy`` + ``StabilizedWorkerLimit``: fast pressure reductions
  with gradual recovery, preventing worker-count oscillation near thresholds.
- ``AuditedShadowPolicy``: every real admission-limit change is written to the
  structured audit log (never every resource sample).
- ``WorkerCapacity``: a hard claim-admission ceiling (1 or 2) persisted atomically
  in a runtime file, independent of the adaptive limit.

``AGENT_TASK_WORKERS`` remains the administrative ceiling. Running tasks are
never cancelled solely to reduce load.
"""

from __future__ import annotations

from pathlib import Path

from adaptive_workers import StabilizedWorkerLimit, WorkerLimitTransition
from audit_log import AuditLogger
from bridge.config import get_settings
from bridge.services.worker_capacity import WorkerCapacity
from resource_monitor import HostResourcePolicy
from shadow_policy_audit import AuditedShadowPolicy
from task_service_v3 import TaskServiceV3


def _configured_workers() -> int:
    return get_settings().agent.task_workers


def _configured_poll_seconds() -> float:
    return get_settings().agent.task_poll_seconds


def _configured_recovery_seconds() -> float:
    return get_settings().agent.worker_recovery_seconds


class TaskService(TaskServiceV3):
    """Production task service with stable live host-pressure-aware concurrency.

    ``AGENT_TASK_WORKERS`` remains the administrative ceiling. A shared
    ``HostResourcePolicy`` continuously derives the safe claim-admission limit
    below that ceiling. Pressure reductions apply immediately; recovery is
    deliberately gradual to prevent worker-count oscillation near thresholds.
    Running tasks are never cancelled solely to reduce load.
    """

    def __init__(
        self,
        store,
        executor,
        poll_seconds: float | None = None,
        max_workers: int | None = None,
        resource_policy: HostResourcePolicy | None = None,
        recovery_seconds: float | None = None,
        audit_logger: AuditLogger | None = None,
        capacity: WorkerCapacity | None = None,
    ) -> None:
        configured_workers = min(2, max(1, int(_configured_workers() if max_workers is None else max_workers)))
        self.capacity = capacity or WorkerCapacity(Path(__file__).resolve().parent / "runtime" / "worker-capacity")
        self.audit_logger = audit_logger or AuditLogger(Path(__file__).resolve().parent / "runtime" / "audit.jsonl")
        base_resource_policy = resource_policy or HostResourcePolicy()
        self.resource_policy = AuditedShadowPolicy(base_resource_policy, self.audit_logger)
        self.worker_limit = StabilizedWorkerLimit(
            configured_workers,
            self.resource_policy,
            recovery_seconds=_configured_recovery_seconds() if recovery_seconds is None else recovery_seconds,
            on_transition=self._audit_worker_transition,
        )
        super().__init__(
            store,
            executor,
            poll_seconds=_configured_poll_seconds() if poll_seconds is None else poll_seconds,
            max_workers=configured_workers,
            worker_limit_provider=self.worker_limit,
        )

    def active_worker_limit(self) -> int:
        return min(super().active_worker_limit(), self.capacity.get())

    def _audit_worker_transition(self, transition: WorkerLimitTransition) -> None:
        """Record only real admission-limit changes, never every resource sample."""
        self.audit_logger.write(
            "adaptive_worker_limit",
            transition.direction,
            details={
                "previous_workers": transition.previous_workers,
                "stable_workers": transition.stable_workers,
                "raw_target_workers": transition.raw_target_workers,
                "pressure": transition.pressure,
                "health_score": transition.health_score,
                "reason": transition.reason,
                "elapsed_since_last_change_seconds": transition.elapsed_since_last_change_seconds,
            },
        )
