"""Adaptive task-service utilities."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from resource_monitor import HostResourcePolicy, WorkerDecision


def choose_worker_limit(configured_workers: int, policy: HostResourcePolicy | None = None) -> int:
    """Return the live safe worker limit for the current host."""
    resource_policy = policy or HostResourcePolicy()
    return resource_policy.decide(configured_workers).allowed_workers


@dataclass(frozen=True)
class WorkerLimitStatus:
    configured_workers: int
    stable_workers: int
    raw_target_workers: int
    pressure: str
    reason: str
    recovery_seconds: float
    recovery_remaining_seconds: float


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
        self._last_decision: WorkerDecision | None = None

    def _recovery_baseline(self, now: float) -> float:
        baseline = self._last_increase_at
        if baseline is None:
            baseline = self._last_pressure_at
        if baseline is None:
            baseline = now
        return baseline

    def __call__(self) -> int:
        now = self._clock()
        decision = self.policy.decide(self.configured_workers)
        self._last_decision = decision
        target = max(1, min(decision.allowed_workers, self.configured_workers))

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

        baseline = self._recovery_baseline(now)
        if self._last_pressure_at is None and self._last_increase_at is None:
            self._last_pressure_at = now
        if now - baseline < self.recovery_seconds:
            return self._current

        self._current = min(target, self._current + 1)
        self._last_increase_at = now
        if self._current >= self.configured_workers:
            self._last_pressure_at = None
        return self._current

    def status(self) -> WorkerLimitStatus:
        """Return observable controller state without mutating admission limits."""
        now = self._clock()
        current = self._current if self._current is not None else self.configured_workers
        decision = self._last_decision
        if decision is None:
            decision = self.policy.decide(self.configured_workers)
            target = max(1, min(decision.allowed_workers, self.configured_workers))
        else:
            target = max(1, min(decision.allowed_workers, self.configured_workers))

        remaining = 0.0
        if target > current:
            baseline = self._recovery_baseline(now)
            remaining = max(0.0, self.recovery_seconds - (now - baseline))

        return WorkerLimitStatus(
            configured_workers=self.configured_workers,
            stable_workers=max(1, current),
            raw_target_workers=target,
            pressure=decision.pressure,
            reason=decision.reason,
            recovery_seconds=self.recovery_seconds,
            recovery_remaining_seconds=remaining,
        )
