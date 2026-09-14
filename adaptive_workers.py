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


@dataclass(frozen=True)
class WorkerLimitTransition:
    previous_workers: int
    stable_workers: int
    raw_target_workers: int
    direction: str
    pressure: str
    reason: str
    elapsed_since_last_change_seconds: float | None


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
        on_transition: Callable[[WorkerLimitTransition], None] | None = None,
    ) -> None:
        self.configured_workers = max(1, min(int(configured_workers), 8))
        self.policy = policy
        self.recovery_seconds = max(1.0, float(recovery_seconds))
        self._clock = clock
        self._on_transition = on_transition
        self._current: int | None = None
        self._last_pressure_at: float | None = None
        self._last_increase_at: float | None = None
        self._last_change_at: float | None = None
        self._last_decision: WorkerDecision | None = None

    def _recovery_baseline(self, now: float) -> float:
        baseline = self._last_increase_at
        if baseline is None:
            baseline = self._last_pressure_at
        if baseline is None:
            baseline = now
        return baseline

    def _transition(self, new_workers: int, decision: WorkerDecision, target: int, now: float) -> int:
        previous = self._current
        self._current = max(1, min(new_workers, self.configured_workers))
        if previous is None or previous == self._current:
            if previous is None:
                self._last_change_at = now
            return self._current

        elapsed = None if self._last_change_at is None else max(0.0, now - self._last_change_at)
        event = WorkerLimitTransition(
            previous_workers=previous,
            stable_workers=self._current,
            raw_target_workers=target,
            direction="decreased" if self._current < previous else "increased",
            pressure=str(getattr(decision, "pressure", "unknown")),
            reason=str(getattr(decision, "reason", "resource policy transition")),
            elapsed_since_last_change_seconds=elapsed,
        )
        self._last_change_at = now
        if self._on_transition is not None:
            try:
                self._on_transition(event)
            except Exception:
                # Observability must never interfere with admission control.
                pass
        return self._current

    def __call__(self) -> int:
        now = self._clock()
        decision = self.policy.decide(self.configured_workers)
        self._last_decision = decision
        target = max(1, min(decision.allowed_workers, self.configured_workers))

        if self._current is None:
            self._current = target
            self._last_change_at = now
            if target < self.configured_workers:
                self._last_pressure_at = now
            return self._current

        if target < self._current:
            self._last_pressure_at = now
            self._last_increase_at = None
            return self._transition(target, decision, target, now)

        if target <= self._current:
            if target < self.configured_workers:
                self._last_pressure_at = now
            return self._current

        baseline = self._recovery_baseline(now)
        if self._last_pressure_at is None and self._last_increase_at is None:
            self._last_pressure_at = now
        if now - baseline < self.recovery_seconds:
            return self._current

        recovered = min(target, self._current + 1)
        self._last_increase_at = now
        result = self._transition(recovered, decision, target, now)
        if result >= self.configured_workers:
            self._last_pressure_at = None
        return result

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
            pressure=str(getattr(decision, "pressure", "unknown")),
            reason=str(getattr(decision, "reason", "resource policy state unavailable")),
            recovery_seconds=self.recovery_seconds,
            recovery_remaining_seconds=remaining,
        )
