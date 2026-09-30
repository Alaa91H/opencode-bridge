"""Dynamic application limits: finite, unlimited (0), or resource-calculated (auto)."""

from __future__ import annotations

from dataclasses import dataclass

LimitValue = int | str


@dataclass(frozen=True)
class ResourceBudget:
    available_memory_bytes: int
    available_disk_bytes: int
    cpu_count: int


@dataclass(frozen=True)
class DynamicLimits:
    attachment_count: LimitValue = "auto"
    total_attachment_bytes: LimitValue = "auto"
    pending_seconds: LimitValue = 0
    workers: LimitValue = "auto"
    output_count: LimitValue = 0

    def resolve(self, name: str, budget: ResourceBudget) -> int:
        value = getattr(self, name)
        if value == 0:
            return 0
        if isinstance(value, int):
            if value < 0:
                raise ValueError("limit cannot be negative")
            return value
        if value != "auto":
            raise ValueError("limit must be integer, 0, or auto")
        if name == "workers":
            return max(1, min(budget.cpu_count * 2, max(1, budget.available_memory_bytes // (512 * 1024 * 1024))))
        if name == "total_attachment_bytes":
            return max(1, min(budget.available_disk_bytes // 4, budget.available_memory_bytes * 8))
        if name == "attachment_count":
            return max(1, min(10000, budget.available_memory_bytes // (8 * 1024 * 1024)))
        return 0

    def allows(self, name: str, requested: int, budget: ResourceBudget) -> bool:
        limit = self.resolve(name, budget)
        return limit == 0 or requested <= limit
