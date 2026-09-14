from __future__ import annotations

import unittest

from task_service_v3 import TaskServiceV3


async def _executor(_task) -> None:
    return None


class LiveAdaptiveWorkerAdmissionTests(unittest.TestCase):
    def test_limit_is_recomputed_without_restarting_service(self) -> None:
        current = {"limit": 3}
        service = TaskServiceV3(
            store=None,  # type: ignore[arg-type]
            executor=_executor,
            max_workers=4,
            worker_limit_provider=lambda: current["limit"],
        )

        self.assertEqual(service.active_worker_limit(), 3)
        self.assertTrue(service.worker_can_claim(3))
        self.assertFalse(service.worker_can_claim(4))

        current["limit"] = 1
        self.assertEqual(service.active_worker_limit(), 1)
        self.assertTrue(service.worker_can_claim(1))
        self.assertFalse(service.worker_can_claim(2))

        current["limit"] = 4
        self.assertEqual(service.active_worker_limit(), 4)
        self.assertTrue(service.worker_can_claim(4))

    def test_provider_cannot_exceed_configured_ceiling(self) -> None:
        service = TaskServiceV3(
            store=None,  # type: ignore[arg-type]
            executor=_executor,
            max_workers=3,
            worker_limit_provider=lambda: 99,
        )
        self.assertEqual(service.active_worker_limit(), 3)

    def test_provider_failure_falls_back_to_one_worker(self) -> None:
        def broken_provider() -> int:
            raise RuntimeError("sampling failed")

        service = TaskServiceV3(
            store=None,  # type: ignore[arg-type]
            executor=_executor,
            max_workers=4,
            worker_limit_provider=broken_provider,
        )
        self.assertEqual(service.active_worker_limit(), 1)
        self.assertTrue(service.worker_can_claim(1))
        self.assertFalse(service.worker_can_claim(2))


if __name__ == "__main__":
    unittest.main()
