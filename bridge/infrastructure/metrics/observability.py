"""Dependency-light observability metrics and Prometheus exposition."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


REQUIRED_METRICS = (
    "queue_depth", "task_latency_seconds", "task_duration_seconds", "retry_count",
    "model_failures", "telegram_failures", "db_latency_seconds", "db_locks",
    "memory_bytes", "cpu_percent", "disk_free_bytes", "attachment_bytes_total",
    "active_workers", "schedule_lag_seconds",
)


@dataclass
class MetricsRegistry:
    values: dict[str, float] = field(default_factory=lambda: {name: 0.0 for name in REQUIRED_METRICS})
    _lock: Lock = field(default_factory=Lock, repr=False)

    def set(self, name: str, value: float) -> None:
        self._validate(name, value)
        with self._lock:
            self.values[name] = float(value)

    def inc(self, name: str, value: float = 1.0) -> None:
        self._validate(name, value)
        with self._lock:
            self.values[name] += float(value)

    def observe(self, name: str, value: float) -> None:
        self.set(name, value)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self.values)

    def prometheus(self, *, prefix: str = "opencode_bridge") -> str:
        snapshot = self.snapshot()
        return "".join(f"{prefix}_{name} {value:g}\n" for name, value in sorted(snapshot.items()))

    def _validate(self, name: str, value: float) -> None:
        if name not in self.values:
            raise KeyError(name)
        if name in {"queue_depth", "retry_count", "model_failures", "telegram_failures",
                    "db_locks", "memory_bytes", "disk_free_bytes", "attachment_bytes_total",
                    "active_workers"} and value < 0:
            raise ValueError(f"{name} cannot be negative")


class PrometheusEndpoint:
    def __init__(self, registry: MetricsRegistry, *, enabled: bool = False) -> None:
        self.registry = registry
        self.enabled = enabled

    def render(self) -> tuple[int, str, str]:
        if not self.enabled:
            return 404, "text/plain; charset=utf-8", "metrics disabled\n"
        return 200, "text/plain; version=0.0.4; charset=utf-8", self.registry.prometheus()
