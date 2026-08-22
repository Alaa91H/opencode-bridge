"""Async worker for queued and scheduled agent tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from task_queue import QueuedTask, TaskQueueStore

log = logging.getLogger(__name__)
TaskExecutor = Callable[[QueuedTask], Awaitable[None]]


class TaskService:
    """Runs persisted tasks in order and wakes promptly on new work."""

    def __init__(self, store: TaskQueueStore, executor: TaskExecutor, poll_seconds: float = 10.0) -> None:
        self.store = store
        self.executor = executor
        self.poll_seconds = poll_seconds
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> int:
        interrupted = await self.store.recover_interrupted()
        self._stop.clear()
        self._worker = asyncio.create_task(self._run(), name="agent-task-scheduler")
        self._wake.set()
        return interrupted

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    def wake(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.store.promote_due()
                task = await self.store.claim_next()
                if task is not None:
                    await self._execute(task)
                    continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("فشل عامل جدولة المهام")
                await asyncio.sleep(min(self.poll_seconds, 5))

    async def _execute(self, task: QueuedTask) -> None:
        try:
            await self.executor(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("فشلت المهمة %s", task.id)
            await self.store.finish(task.id, success=False, error=type(exc).__name__)
        else:
            current = await self.store.get(task.id)
            if current and current.status == "running":
                await self.store.finish(task.id, success=True)
