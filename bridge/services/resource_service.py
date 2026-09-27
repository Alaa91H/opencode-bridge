"""Application service for host-resource diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ResourceSnapshot:
    configured_workers: int
    decision: Any
    controller: Any | None
    shadow_readiness: Any | None


class ResourceStatusService:
    def __init__(
        self,
        policy: Any,
        *,
        configured_workers: Callable[[], int],
        controller_status: Callable[[], Any | None],
        shadow_readiness: Callable[[], Any | None],
    ) -> None:
        self.policy = policy
        self.configured_workers = configured_workers
        self.controller_status = controller_status
        self.shadow_readiness = shadow_readiness

    def snapshot(self) -> ResourceSnapshot:
        configured = self.configured_workers()
        return ResourceSnapshot(
            configured_workers=configured,
            decision=self.policy.decide(configured),
            controller=self.controller_status(),
            shadow_readiness=self.shadow_readiness(),
        )
