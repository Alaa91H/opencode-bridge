"""Compatibility entry point for the bounded V3 task worker pool."""

from __future__ import annotations

import os
from pathlib import Path

from adaptive_workers import StabilizedWorkerLimit, WorkerLimitTransition
from audit_log import AuditLogger
from resource_monitor import HostResourcePolicy
from task_service_v3 import TaskServiceV3


def _configured_workers() -> int:
    try:
        value = int(os.environ.get("AGENT_TASK_WORKERS", "2"))
    except ValueError:
        value = 2
    return max(1, min(value, 8))


def _configured_poll_seconds() -> float:
    try:
        value = float(os.environ.get("AGENT_TASK_POLL_SECONDS", "5"))
    except ValueError:
        value = 5.0
    return max(0.5, min(value, 60.0))


def _configured_recovery_seconds() -> float:
    try:
        value = float(os.environ.get("AGENT_WORKER_RECOVERY_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return max(1.0, min(value, 600.0))


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
    ) -> None:
        configured_workers = _configured_workers() if max_workers is None else max_workers
        self.resource_policy = resource_policy or HostResourcePolicy()
        self.audit_logger = audit_logger or AuditLogger(Path(__file__).resolve().parent / "runtime" / "audit.jsonl")
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
