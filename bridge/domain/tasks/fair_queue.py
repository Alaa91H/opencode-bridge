"""Priority, aging and owner-fair task selection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from time import monotonic

from bridge.domain.tasks.resource_scheduler import ResourceCost


class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass(frozen=True)
class FairTask:
    task_id: str
    owner: str
    priority: Priority = Priority.NORMAL
    enqueued_at: float = 0.0
    interactive: bool = False
    scheduled: bool = False
    cost: ResourceCost = ResourceCost()  # noqa: RUF009 - ResourceCost is frozen and immutable


class FairQueuePolicy:
    def __init__(self, *, aging_seconds: float = 60.0, interactive_boost: float = 2.0,
                 owner_penalty: float = 1.0, resource_penalty: float = .5) -> None:
        if aging_seconds <= 0:
            raise ValueError("aging_seconds must be positive")
        self.aging_seconds = aging_seconds
        self.interactive_boost = interactive_boost
        self.owner_penalty = owner_penalty
        self.resource_penalty = resource_penalty

    @staticmethod
    def _resource_weight(cost: ResourceCost) -> float:
        return cost.cpu + cost.ram_mb / 1024 + cost.disk_mb / 10240 + cost.io + cost.model + cost.media

    def score(self, task: FairTask, *, now: float, owner_running: int = 0) -> float:
        age = max(0.0, now - task.enqueued_at) / self.aging_seconds
        # Aging is unbounded: an old task eventually outranks newer work.
        score = float(task.priority) * 4.0 + age
        if task.interactive:
            score += self.interactive_boost
        score -= owner_running * self.owner_penalty
        score -= self._resource_weight(task.cost) * self.resource_penalty
        return score

    def order(self, tasks: Iterable[FairTask], *, owner_running: dict[str, int] | None = None,
              now: float | None = None) -> tuple[FairTask, ...]:
        owner_running = owner_running or {}
        now = monotonic() if now is None else now
        return tuple(sorted(
            tasks,
            key=lambda task: (
                -self.score(task, now=now, owner_running=owner_running.get(task.owner, 0)),
                task.enqueued_at,
                task.owner,
                task.task_id,
            ),
        ))

    def choose(self, tasks: Iterable[FairTask], **kwargs) -> FairTask | None:
        ordered = self.order(tasks, **kwargs)
        return ordered[0] if ordered else None
