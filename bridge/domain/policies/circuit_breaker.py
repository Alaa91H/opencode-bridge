"""Independent circuit breaker primitive for external dependencies."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpen(RuntimeError):
    pass


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_successes: int = 1


@dataclass
class CircuitBreakerMetrics:
    successes: int = 0
    failures: int = 0
    rejected: int = 0
    opened: int = 0
    recovered: int = 0


class CircuitBreaker:
    def __init__(self, name: str, config: CircuitBreakerConfig = CircuitBreakerConfig(),
                 *, clock: Callable[[], float] = time.monotonic,
                 on_event: Callable[[str, str], None] | None = None) -> None:
        self.name = name
        self.config = config
        self.clock = clock
        self.on_event = on_event
        self.state = CircuitState.CLOSED
        self.metrics = CircuitBreakerMetrics()
        self._failures = 0
        self._half_open_successes = 0
        self._opened_at: float | None = None

    def _emit(self, event: str) -> None:
        if self.on_event:
            self.on_event(self.name, event)

    def allow(self) -> bool:
        if self.state is CircuitState.OPEN:
            assert self._opened_at is not None
            if self.clock() - self._opened_at >= self.config.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self._half_open_successes = 0
                self._emit("half_open")
                return True
            self.metrics.rejected += 1
            return False
        return True

    def before_call(self) -> None:
        if not self.allow():
            raise CircuitOpen(f"circuit {self.name} is open")

    def success(self) -> None:
        self.metrics.successes += 1
        if self.state is CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self.config.half_open_successes:
                self.state = CircuitState.CLOSED
                self._failures = 0
                self.metrics.recovered += 1
                self._emit("closed")
        elif self.state is CircuitState.CLOSED:
            self._failures = 0

    def failure(self) -> None:
        self.metrics.failures += 1
        self._failures += 1
        if self.state is CircuitState.HALF_OPEN or self._failures >= self.config.failure_threshold:
            self.state = CircuitState.OPEN
            self._opened_at = self.clock()
            self.metrics.opened += 1
            self._emit("open")


class CircuitBreakerRegistry:
    DEFAULT_DEPENDENCIES = ("telegram", "opencode", "model_provider", "github", "storage")

    def __init__(self, config: CircuitBreakerConfig = CircuitBreakerConfig(), **kwargs) -> None:
        self.breakers = {name: CircuitBreaker(name, config, **kwargs) for name in self.DEFAULT_DEPENDENCIES}

    def __getitem__(self, dependency: str) -> CircuitBreaker:
        return self.breakers[dependency]
