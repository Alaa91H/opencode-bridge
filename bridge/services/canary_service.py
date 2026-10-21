"""Canary/shadow rollout policy for Bridge 2.0."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class CanaryMetrics:
    error_rate: float
    scheduler_mismatch_rate: float
    p95_latency_ratio: float


@dataclass
class CanaryPolicy:
    percentage: int = 1
    max_error_rate: float = 0.02
    max_scheduler_mismatch_rate: float = 0.0
    max_p95_latency_ratio: float = 1.5

    def selected(self, task_key: str) -> bool:
        if not 0 <= self.percentage <= 100:
            raise ValueError("percentage must be 0..100")
        bucket = int(hashlib.sha256(task_key.encode()).hexdigest()[:8], 16) % 100
        return bucket < self.percentage

    def should_rollback(self, metrics: CanaryMetrics) -> bool:
        return (
            metrics.error_rate > self.max_error_rate
            or metrics.scheduler_mismatch_rate > self.max_scheduler_mismatch_rate
            or metrics.p95_latency_ratio > self.max_p95_latency_ratio
        )

    def expand(self, step: int = 5) -> int:
        self.percentage = min(100, self.percentage + step)
        return self.percentage


@dataclass(frozen=True)
class ShadowResult:
    legacy_schedule_keys: tuple[str, ...]
    v2_schedule_keys: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return self.legacy_schedule_keys == self.v2_schedule_keys
