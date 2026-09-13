"""Compatibility entry point for the bounded V3 task worker pool."""

from __future__ import annotations

import os

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
    """Production task service with environment-tunable bounded concurrency."""

    def __init__(self, store, executor, poll_seconds: float | None = None, max_workers: int | None = None) -> None:
        super().__init__(
            store,
            executor,
            poll_seconds=_configured_poll_seconds() if poll_seconds is None else poll_seconds,
            max_workers=_configured_workers() if max_workers is None else max_workers,
        )
