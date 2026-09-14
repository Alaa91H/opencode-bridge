"""Adaptive task-service utilities."""

from __future__ import annotations

import time
from collections.abc import Callable

from resource_monitor import HostResourcePolicy


def choose_worker_limit(configured_workers: int, policy: HostResourcePolicy | None = None) -> int:
    """Return the live safe worker limit for the current host."""
    resource_policy = policy or HostResourcePolicy()
    return resource_policy.decide(configured_workers).allowed_workers


class StabilizedWorkerLimit:
    """Apply fast pressure reductions and deliberately slow worker recovery.

    Resource pressure can hover around a threshold on small VPS hosts. Applying
    every raw sample directly can oscillate worker admission and immediately
    consume memory that has only just become available. Reductions therefore
    take effect at once, while increases require a continuous healthy window
    and recover only one worker at a time.
    """

    def __init__(
        self,
        configured_workers: int,
        policy: HostResourcePolicy,
        recovery_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.configured_workers = max(1, min(int(configured_workers), 8))
        self.policy = policy
        self.recovery_seconds = max(1.0, float(recovery_seconds))
        self._clock = clock
        self._current: int | None = None
        self._last_pressure_at: float | None = None
        self._last_increase_at: float | None = None

    def __call__(self) -> int:
        now = self._clock()
        target = self.policy.decide(self.configured_workers).allowed_workers
        target = max(1, min(target, self.configured_workers))

        if self._current is None:
            self._current = target
            if target < self.configured_workers:
                self._last_pressure_at = now
            return self._current

        if target < self._current:
            self._current = target
            self._last_pressure_at = now
            self._last_increase_at = None
            return self._current

        if target <= self._current:
            if target < self.configured_workers:
                self._last_pressure_at = now
            return self._current

        # A higher raw target is only trusted after a continuous recovery
        # interval. Subsequent increases are also spaced by that interval so a
        # suddenly idle sample cannot jump from one worker to the full ceiling.
        baseline = self._last_increase_at
        if baseline is None:
            baseline = self._last_pressure_at
        if baseline is None:
            baseline = now
            self._last_pressure_at = now
        if now - baseline < self.recovery_seconds:
            return self._current

        self._current = min(target, self._current + 1)
        self._last_increase_at = now
        if self._current >= self.configured_workers:
            self._last_pressure_at = None
        return self._current
