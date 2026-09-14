"""Compatibility entry point for the bounded V3 task worker pool."""

from __future__ import annotations

import os

from adaptive_workers import choose_worker_limit
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


class TaskService(TaskServiceV3):
    """Production task service with live host-pressure-aware concurrency.

    ``AGENT_TASK_WORKERS`` remains the administrative ceiling. A shared
    ``HostResourcePolicy`` continuously derives the safe claim-admission limit
    below that ceiling, so pressure changes take effect without restarting the
    Telegram bridge. Running tasks are never cancelled solely to reduce load.
    """

    def __init__(
        self,
        store,
        executor,
        poll_seconds: float | None = None,
        max_workers: int | None = None,
        resource_policy: HostResourcePolicy | None = None,
    ) -> None:
        configured_workers = _configured_workers() if max_workers is None else max_workers
        self.resource_policy = resource_policy or HostResourcePolicy()
        super().__init__(
            store,
            executor,
            poll_seconds=_configured_poll_seconds() if poll_seconds is None else poll_seconds,
            max_workers=configured_workers,
            worker_limit_provider=lambda: choose_worker_limit(configured_workers, self.resource_policy),
        )
