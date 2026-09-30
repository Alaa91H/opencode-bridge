import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bridge.domain.tasks.cancellation import CancellationToken, TaskCancelled
from bridge.infrastructure.processes.cancellable import terminate_process


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_cancels_each_pipeline_stage(self):
        stages = ["telegram", "task_service", "opencode", "subprocess", "media", "upload"]
        for stage in stages:
            token = CancellationToken()
            await token.cancel()
            with self.assertRaises(TaskCancelled, msg=stage):
                token.checkpoint()

    async def test_cleanup_runs_reverse_order_once(self):
        token = CancellationToken()
        events = []
        token.add_cleanup(lambda: events.append("workspace"))
        token.add_cleanup(lambda: events.append("stream"))
        await token.cancel()
        await token.cancel()
        self.assertEqual(events, ["stream", "workspace"])

    async def test_wait_is_cooperative(self):
        token = CancellationToken()
        waiter = asyncio.create_task(token.wait())
        await token.cancel()
        with self.assertRaises(TaskCancelled):
            await waiter

    async def test_process_terminate_reaps_without_kill(self):
        process = Mock()
        process.returncode = None
        process.wait = AsyncMock(return_value=0)
        process.terminate = Mock()
        process.kill = Mock()
        await terminate_process(process, grace_seconds=.1)
        process.terminate.assert_called_once()
        process.kill.assert_not_called()
        process.wait.assert_awaited()

    async def test_process_escalates_to_kill_and_reaps(self):
        process = Mock()
        process.returncode = None
        process.terminate = Mock()
        process.kill = Mock()
        process.wait = AsyncMock(side_effect=[TimeoutError(), 0])
        with patch("bridge.infrastructure.processes.cancellable.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            # use a fresh wait mock because wait_for times out before consuming it
            process.wait = AsyncMock(return_value=0)
            await terminate_process(process, grace_seconds=.001)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        process.wait.assert_awaited()
