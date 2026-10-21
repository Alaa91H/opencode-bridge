"""Pressure-aware multidimensional resource scheduling."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceCost:
    cpu: float = 0.0
    ram_mb: int = 0
    disk_mb: int = 0
    io: float = 0.0
    model: float = 0.0
    media: float = 0.0


@dataclass(frozen=True)
class ResourceCapacity:
    cpu: float
    ram_mb: int
    disk_mb: int
    io: float = 1.0
    model: float = 1.0
    media: float = 1.0


@dataclass(frozen=True)
class ResourcePressure:
    cpu: float = 0.0
    ram: float = 0.0
    disk: float = 0.0
    io: float = 0.0


_DEFAULT_RESOURCEPRESSURE = ResourcePressure()  # frozen config: safe to share as a default


@dataclass(frozen=True)
class ResourceTask:
    task_id: str
    cost: ResourceCost


class ResourceScheduler:
    def __init__(self, capacity: ResourceCapacity, *, ram_guard: float = .90,
                 disk_guard: float = .90) -> None:
        self.capacity = capacity
        self.ram_guard = ram_guard
        self.disk_guard = disk_guard

    def available(self, pressure: ResourcePressure) -> ResourceCapacity:
        return ResourceCapacity(
            cpu=max(0.0, self.capacity.cpu * (1 - pressure.cpu)),
            ram_mb=max(0, int(self.capacity.ram_mb * (min(self.ram_guard, 1 - pressure.ram)))),
            disk_mb=max(0, int(self.capacity.disk_mb * (min(self.disk_guard, 1 - pressure.disk)))),
            io=max(0.0, self.capacity.io * (1 - pressure.io)),
            model=self.capacity.model,
            media=self.capacity.media,
        )

    @staticmethod
    def _fits(cost: ResourceCost, available: ResourceCapacity) -> bool:
        return (
            cost.cpu <= available.cpu and cost.ram_mb <= available.ram_mb
            and cost.disk_mb <= available.disk_mb and cost.io <= available.io
            and cost.model <= available.model and cost.media <= available.media
        )

    @staticmethod
    def _subtract(available: ResourceCapacity, cost: ResourceCost) -> ResourceCapacity:
        return ResourceCapacity(
            available.cpu - cost.cpu, available.ram_mb - cost.ram_mb,
            available.disk_mb - cost.disk_mb, available.io - cost.io,
            available.model - cost.model, available.media - cost.media)

    @staticmethod
    def _weight(cost: ResourceCost, capacity: ResourceCapacity) -> float:
        return max(
            cost.cpu / max(capacity.cpu, .001),
            cost.ram_mb / max(capacity.ram_mb, 1),
            cost.disk_mb / max(capacity.disk_mb, 1),
            cost.io / max(capacity.io, .001),
            cost.model / max(capacity.model, .001),
            cost.media / max(capacity.media, .001),
        )

    def pack(self, tasks: Iterable[ResourceTask], pressure: ResourcePressure = _DEFAULT_RESOURCEPRESSURE) -> tuple[ResourceTask, ...]:
        available = self.available(pressure)
        # Best-fit-decreasing by dominant resource share reduces fragmentation.
        ranked = sorted(tasks, key=lambda t: (-self._weight(t.cost, self.capacity), t.task_id))
        selected: list[ResourceTask] = []
        for task in ranked:
            if self._fits(task.cost, available):
                selected.append(task)
                available = self._subtract(available, task.cost)
        return tuple(selected)
