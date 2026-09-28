"""Independent liveness/readiness and aggregate dependency health."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    state: HealthState
    detail: str = ""
    required: bool = True


@dataclass(frozen=True)
class HealthReport:
    state: HealthState
    components: tuple[ComponentHealth, ...]


class HealthService:
    COMPONENTS = ("telegram", "db", "opencode", "disk", "workers", "scheduler", "model_catalog")

    def liveness(self) -> bool:
        return True

    def readiness(self, checks: Mapping[str, ComponentHealth]) -> bool:
        return all(
            checks.get(name, ComponentHealth(name, HealthState.UNHEALTHY)).state != HealthState.UNHEALTHY
            for name in self.COMPONENTS
            if checks.get(name, ComponentHealth(name, HealthState.UNHEALTHY)).required
        )

    def health(self, checks: Mapping[str, ComponentHealth]) -> HealthReport:
        components = tuple(
            checks.get(name, ComponentHealth(name, HealthState.UNHEALTHY, "missing check"))
            for name in self.COMPONENTS
        )
        required_bad = any(c.required and c.state == HealthState.UNHEALTHY for c in components)
        any_degraded = any(c.state != HealthState.HEALTHY for c in components)
        state = HealthState.UNHEALTHY if required_bad else (
            HealthState.DEGRADED if any_degraded else HealthState.HEALTHY)
        return HealthReport(state, components)


def systemd_readiness_message(ready: bool, status: str = "") -> str:
    return f"READY={1 if ready else 0}\nSTATUS={status or ('Ready' if ready else 'Not ready')}"
