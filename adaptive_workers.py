"""Adaptive task-service utilities."""

from __future__ import annotations

from resource_monitor import HostResourcePolicy


def choose_worker_limit(configured_workers: int, policy: HostResourcePolicy | None = None) -> int:
    """Return the live safe worker limit for the current host."""
    resource_policy = policy or HostResourcePolicy()
    return resource_policy.decide(configured_workers).allowed_workers
