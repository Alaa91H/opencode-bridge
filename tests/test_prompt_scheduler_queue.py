"""Regression tests for the prompt scheduler queue's idle behaviour."""

from __future__ import annotations

import asyncio
import time
import unittest
from datetime import UTC, datetime, timedelta

from bridge.services.prompt_scheduler.scheduler_engine import (
    Priority,
    PriorityTaskQueue,
    ScheduledPrompt,
    ScheduleType,
)


def _prompt(
    *,
    priority: Priority = Priority.NORMAL,
    execute_at: datetime | None = None,
    status: str = "pending",
) -> ScheduledPrompt:
    return ScheduledPrompt(
        id=f"task-{time.monotonic_ns()}",
        prompt="test",
        schedule_type=ScheduleType.DELAYED if execute_at else ScheduleType.IMMEDIATE,
        priority=priority,
        execute_at=execute_at,
        status=status,
    )


class PriorityQueueIdleTests(unittest.IsolatedAsyncioTestCase):
    async def test_future_head_does_not_block_dequeue(self) -> None:
        """A task scheduled in the future must return None immediately."""
        queue = PriorityTaskQueue()
        await queue.enqueue(
            _prompt(
                priority=Priority.CRITICAL,
                execute_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        started = time.monotonic()
        result = await asyncio.wait_for(queue.dequeue(), timeout=0.5)
        elapsed = time.monotonic() - started
        self.assertIsNone(result)
        self.assertLess(elapsed, 0.1)

    async def test_due_low_priority_runs_before_future_critical(self) -> None:
        queue = PriorityTaskQueue()
        future = _prompt(
            priority=Priority.CRITICAL,
            execute_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        due = _prompt(priority=Priority.LOW)
        await queue.enqueue(future)
        await queue.enqueue(due)

        chosen = await queue.dequeue()
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.id, due.id)
        # The future task remains queued for its own time.
        self.assertIs(await queue.get_task(future.id), future)

    async def test_due_tasks_keep_priority_order(self) -> None:
        queue = PriorityTaskQueue()
        low = _prompt(priority=Priority.LOW)
        critical = _prompt(priority=Priority.CRITICAL)
        normal = _prompt(priority=Priority.NORMAL)
        for task in (low, critical, normal):
            await queue.enqueue(task)

        order = []
        for _ in range(3):
            chosen = await queue.dequeue()
            assert chosen is not None
            order.append(chosen.priority)
        self.assertEqual(order, [Priority.CRITICAL, Priority.NORMAL, Priority.LOW])

    async def test_cancelled_tasks_are_dropped_from_the_heap(self) -> None:
        queue = PriorityTaskQueue()
        task = _prompt()
        await queue.enqueue(task)
        self.assertTrue(await queue.cancel_task(task.id))
        self.assertIsNone(await queue.dequeue())
        self.assertIsNone(await queue.get_task(task.id))


class PriorityQueueTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_due_in_reports_nearest_delay(self) -> None:
        queue = PriorityTaskQueue()
        now = datetime.now(UTC)
        await queue.enqueue(_prompt(execute_at=now + timedelta(seconds=120)))
        await queue.enqueue(_prompt(execute_at=now + timedelta(seconds=30)))
        delay = await queue.next_due_in()
        assert delay is not None
        self.assertGreater(delay, 20)
        self.assertLessEqual(delay, 31)

    async def test_next_due_in_is_zero_for_immediate_work(self) -> None:
        queue = PriorityTaskQueue()
        await queue.enqueue(_prompt())
        self.assertEqual(await queue.next_due_in(), 0.0)

    async def test_next_due_in_is_none_when_idle(self) -> None:
        self.assertIsNone(await PriorityTaskQueue().next_due_in())

    async def test_wait_for_work_wakes_on_enqueue(self) -> None:
        queue = PriorityTaskQueue()

        async def enqueue_later() -> None:
            await asyncio.sleep(0.02)
            await queue.enqueue(_prompt())

        waiter = asyncio.create_task(queue.wait_for_work(5.0))
        enqueuer = asyncio.create_task(enqueue_later())
        self.assertTrue(await asyncio.wait_for(waiter, timeout=1.0))
        await enqueuer

    async def test_wait_for_work_times_out_without_events(self) -> None:
        queue = PriorityTaskQueue()
        started = time.monotonic()
        self.assertFalse(await queue.wait_for_work(0.05))
        self.assertLess(time.monotonic() - started, 0.5)


class PromptSchedulerExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_for(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.02)
        self.fail("condition was not reached before the deadline")

    async def test_registered_executor_runs_concurrently_within_capacity(self) -> None:
        from bridge.services.prompt_scheduler.scheduler_engine import SmartPromptScheduler

        scheduler = SmartPromptScheduler(
            max_concurrent_tasks=2, idle_wait_seconds=0.25
        )
        active = 0
        peak = 0
        executed: list[str] = []

        async def executor(task: ScheduledPrompt) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.3)
            active -= 1
            executed.append(task.id)
            return "ok"

        scheduler.register_executor(executor)
        for _ in range(4):
            await scheduler.schedule_prompt("work")
        await scheduler.start_scheduler()
        try:
            await self._wait_for(lambda: len(executed) == 4)
        finally:
            await scheduler.stop_scheduler()

        self.assertEqual(len(executed), 4)
        self.assertEqual(peak, 2)

    async def test_task_timeout_marks_the_task_failed(self) -> None:
        from bridge.services.prompt_scheduler.scheduler_engine import SmartPromptScheduler

        scheduler = SmartPromptScheduler(
            max_concurrent_tasks=1, task_timeout_seconds=0.1
        )
        tasks: list[ScheduledPrompt] = []

        async def executor(task: ScheduledPrompt) -> str:
            tasks.append(task)
            await asyncio.sleep(1.0)
            return "late"

        scheduler.register_executor(executor)
        await scheduler.schedule_prompt("slow")
        await scheduler.start_scheduler()
        try:
            await self._wait_for(lambda: bool(tasks) and tasks[0].status == "failed")
        finally:
            await scheduler.stop_scheduler()
        self.assertTrue(tasks[0].error)

    async def test_cancelling_a_running_task_stops_it(self) -> None:
        from bridge.services.prompt_scheduler.scheduler_engine import SmartPromptScheduler

        scheduler = SmartPromptScheduler(max_concurrent_tasks=1)
        started = asyncio.Event()

        async def executor(task: ScheduledPrompt) -> str:
            started.set()
            await asyncio.sleep(30)
            return "never"

        scheduler.register_executor(executor)
        task_id = await scheduler.schedule_prompt("long")
        await scheduler.start_scheduler()
        try:
            await asyncio.wait_for(started.wait(), timeout=2.0)
            self.assertTrue(await asyncio.wait_for(scheduler.cancel_task(task_id), timeout=2.0))
            self.assertEqual(scheduler._running_prompts, {})
        finally:
            await scheduler.stop_scheduler()

    async def test_dependency_unblocks_after_prerequisite_finishes(self) -> None:
        from bridge.services.prompt_scheduler.scheduler_engine import SmartPromptScheduler

        scheduler = SmartPromptScheduler(idle_wait_seconds=0.25)
        order: list[str] = []

        async def executor(task: ScheduledPrompt) -> str:
            await asyncio.sleep(0.05)
            order.append(task.prompt)
            return "ok"

        scheduler.register_executor(executor)
        first = await scheduler.schedule_prompt("first")
        await scheduler.schedule_prompt("second", dependencies=[first])
        await scheduler.start_scheduler()
        try:
            await self._wait_for(lambda: order == ["first", "second"], timeout=10.0)
        finally:
            await scheduler.stop_scheduler()
        self.assertEqual(order, ["first", "second"])

    async def test_recurring_task_is_requeued_after_execution(self) -> None:
        from bridge.services.prompt_scheduler.scheduler_engine import SmartPromptScheduler

        scheduler = SmartPromptScheduler(idle_wait_seconds=0.25)
        task_id = await scheduler.schedule_prompt(
            "repeat", schedule_type=ScheduleType.RECURRING, interval="1h"
        )
        await scheduler.start_scheduler()
        try:
            await self._wait_for(
                lambda: (task := scheduler.queue._task_map.get(task_id)) is not None
                and task.next_execution is not None
            )
        finally:
            await scheduler.stop_scheduler()
        task = scheduler.queue._task_map.get(task_id)
        assert task is not None
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.execution_count, 1)


if __name__ == "__main__":
    unittest.main()

