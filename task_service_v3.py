"""Bounded worker pool for independent Telegram agent tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from task_queue import QueuedTask, TaskQueueStore

log = logging.getLogger(__name__)
TaskExecutor = Callable[[QueuedTask], Awaitable[None]]
WorkerLimitProvider = Callable[[], int]


class TaskServiceV3:
    """Process independent owners concurrently while preserving per-owner order.

    ``max_workers`` is the hard concurrency ceiling. When a live
    ``worker_limit_provider`` is supplied, workers above the current safe limit
    remain idle and re-check before claiming new work. Running tasks are never
    cancelled solely because host pressure increased.
    """

    def __init__(
        self,
        store: TaskQueueStore,
        executor: TaskExecutor,
        poll_seconds: float = 5.0,
        max_workers: int = 2,
        worker_limit_provider: WorkerLimitProvider | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.poll_seconds = max(0.5, float(poll_seconds))
        self.max_workers = max(1, min(int(max_workers), 8))
        self.worker_limit_provider = worker_limit_provider
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._workers: list[asyncio.Task[None]] = []

    def active_worker_limit(self) -> int:
        """Return the current claim-admission limit within the configured ceiling."""
        if self.worker_limit_provider is None:
            return self.max_workers
        try:
            requested = int(self.worker_limit_provider())
        except Exception as exc:
            log.warning("adaptive worker limit provider failed: %s", type(exc).__name__)
            return 1
        return max(1, min(requested, self.max_workers))

    def worker_can_claim(self, worker_id: int) -> bool:
        """Return whether this worker may claim a new task right now."""
        return 1 <= worker_id <= self.active_worker_limit()

    async def start(self) -> int:
        interrupted = await self.store.recover_interrupted()
        self._stop.clear()
        if not self._workers:
            self._workers = [
                asyncio.create_task(self._run(index), name=f"agent-worker-{index}")
                for index in range(1, self.max_workers + 1)
            ]
        self._wake.set()
        return interrupted

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def _wait_for_work(self) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
        except asyncio.TimeoutError:
            pass

    async def _run(self, worker_id: int) -> None:
        while not self._stop.is_set():
            try:
                if not self.worker_can_claim(worker_id):
                    await self._wait_for_work()
                    continue
                await self.store.promote_due()
                task = await self.store.claim_next()
                if task is not None:
                    log.debug("worker %s claimed task %s", worker_id, task.id)
                    await self._execute(task)
                    self._wake.set()
                    continue
                await self._wait_for_work()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("task worker %s failed", worker_id)
                await asyncio.sleep(min(self.poll_seconds, 5.0))

    async def _execute(self, task: QueuedTask) -> None:
        try:
            await self.executor(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("task %s failed", task.id)
            await self.store.finish(task.id, success=False, error=type(exc).__name__)
        else:
            current = await self.store.get(task.id)
            if current and current.status == "running":
                await self.store.finish(task.id, success=True)
