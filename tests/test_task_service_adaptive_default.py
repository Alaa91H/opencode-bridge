from __future__ import annotations

import unittest

from resource_monitor import ResourceSnapshot, WorkerDecision
from task_service import TaskService


async def _executor(_task) -> None:
    return None


class SequencedPolicy:
    def __init__(self, limits: list[int]) -> None:
        self.limits = limits
        self.index = 0

    def decide(self, configured_workers: int) -> WorkerDecision:
        limit = self.limits[min(self.index, len(self.limits) - 1)]
        self.index += 1
        snapshot = ResourceSnapshot(
            total_memory_mib=4096,
            available_memory_mib=3072,
            swap_total_mib=1024,
            swap_free_mib=1024,
            cpu_count=4,
            load1=0.1,
            disk_free_percent=80.0,
            memory_psi_avg10=0.0,
        )
        return WorkerDecision(
            allowed_workers=limit,
            configured_workers=configured_workers,
            pressure="normal",
            reason="test",
            snapshot=snapshot,
        )


class ProductionAdaptiveWorkerTests(unittest.TestCase):
    def test_default_service_uses_live_resource_policy(self) -> None:
        policy = SequencedPolicy([3, 1, 4])
        service = TaskService(
            store=None,  # type: ignore[arg-type]
            executor=_executor,
            max_workers=4,
            resource_policy=policy,  # type: ignore[arg-type]
        )

        self.assertEqual(service.active_worker_limit(), 3)
        self.assertEqual(service.active_worker_limit(), 1)
        self.assertEqual(service.active_worker_limit(), 4)

    def test_resource_policy_cannot_exceed_admin_ceiling(self) -> None:
        policy = SequencedPolicy([8])
        service = TaskService(
            store=None,  # type: ignore[arg-type]
            executor=_executor,
            max_workers=2,
            resource_policy=policy,  # type: ignore[arg-type]
        )
        self.assertEqual(service.active_worker_limit(), 2)


if __name__ == "__main__":
    unittest.main()
