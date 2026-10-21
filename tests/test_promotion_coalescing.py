"""Schedule promotion runs once per interval, not once per worker."""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from task_service_v3 import TaskServiceV3


class _Store:
    """Records promotion and claim calls; ``claim_next`` never returns work."""

    def __init__(self, *, error: Exception | None = None, delay: float = 0.0) -> None:
        self.promotions = 0
        self.claims = 0
        self.error = error
        self.delay = delay

    async def promote_due(self) -> int:
        self.promotions += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return 0

    async def claim_next(self, *args, **kwargs):
        self.claims += 1
        return None

    async def recover_interrupted(self) -> int:
        return 0


class PromotionCoalescingTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, store, *, workers: int = 3, poll: float = 5.0) -> TaskServiceV3:
        async def executor(_task):
            return None

        return TaskServiceV3(store, executor, poll_seconds=poll, max_workers=workers)

    async def test_concurrent_workers_promote_once_per_interval(self) -> None:
        store = _Store(delay=0.05)
        service = self._service(store, workers=3)
        await asyncio.gather(*(service._promote_if_due() for _ in range(3)))
        self.assertEqual(store.promotions, 1)

    async def test_repeated_calls_inside_the_interval_are_skipped(self) -> None:
        store = _Store()
        service = self._service(store, workers=1, poll=60.0)
        for _ in range(50):
            await service._promote_if_due()
        self.assertEqual(store.promotions, 1)

    async def test_the_interval_still_expires(self) -> None:
        store = _Store()
        service = self._service(store, workers=1, poll=0.5)
        await service._promote_if_due()
        service._last_promote -= 0.6
        await service._promote_if_due()
        self.assertEqual(store.promotions, 2)

    async def test_failed_promotion_is_retried_not_skipped(self) -> None:
        store = _Store(error=RuntimeError("database is locked"))
        service = self._service(store, workers=1, poll=60.0)
        with self.assertRaises(RuntimeError):
            await service._promote_if_due()
        self.assertEqual(store.promotions, 1)
        store.error = None
        # No blind window: the next tick retries immediately.
        await service._promote_if_due()
        self.assertEqual(store.promotions, 2)

    async def test_first_tick_promotes_without_waiting(self) -> None:
        store = _Store()
        service = self._service(store, workers=2, poll=5.0)
        self.assertEqual(service._last_promote, 0.0)
        await service._promote_if_due()
        self.assertEqual(store.promotions, 1)


class WorkerLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_pool_promotes_far_less_often_than_it_polls(self) -> None:
        store = _Store()
        # poll_seconds is clamped to a 0.5s floor by TaskServiceV3, so the test
        # waits for real poll rounds instead of assuming a tiny interval.
        service = TaskServiceV3(store, lambda _t: asyncio.sleep(0), poll_seconds=0.5, max_workers=3)
        await service.start()
        deadline = time.monotonic() + 5.0
        while store.claims < 10 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        await service.stop()
        self.assertGreaterEqual(store.claims, 10)
        # Before the fix every poll of every worker promoted: claims == promotions.
        self.assertLess(store.promotions, store.claims)
        self.assertGreaterEqual(store.promotions, 1)

    async def test_worker_survives_a_failing_promotion(self) -> None:
        store = _Store(error=RuntimeError("locked"))
        service = TaskServiceV3(store, lambda _t: asyncio.sleep(0), poll_seconds=0.5, max_workers=1)
        with patch("task_service_v3.log") as logger:
            await service.start()
            deadline = time.monotonic() + 3.0
            while store.promotions < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            await service.stop()
        self.assertGreaterEqual(store.promotions, 2)
        self.assertTrue(logger.exception.called)


if __name__ == "__main__":
    unittest.main()
