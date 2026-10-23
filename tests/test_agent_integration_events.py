"""Regression tests for event-driven agent integration processing."""

from __future__ import annotations

import asyncio
import time
import unittest

from bridge.services.agent_integration.agent_integration_engine import AgentSystemIntegration


class EventProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_emitted_event_is_acknowledged_without_polling_delay(self) -> None:
        integration = AgentSystemIntegration(event_poll_seconds=60.0)
        await integration.start()
        try:
            started = time.monotonic()
            await integration.emit_system_event("task_completed", {"task": 1})
            for _ in range(100):
                if integration._system_events[0].acknowledged:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(integration._system_events[0].acknowledged)
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            await integration.stop()

    async def test_stop_wakes_an_idle_processor_immediately(self) -> None:
        integration = AgentSystemIntegration(event_poll_seconds=300.0)
        await integration.start()
        started = time.monotonic()
        await integration.stop()
        self.assertLess(time.monotonic() - started, 1.0)

    async def test_acknowledged_history_is_bounded(self) -> None:
        integration = AgentSystemIntegration(event_history_limit=3)
        for index in range(10):
            await integration.emit_system_event("noise", {"index": index})
        for event in list(integration._system_events):
            await integration.acknowledge_event(event.id)
        await integration.emit_system_event("latest", {})
        self.assertLessEqual(len(integration._system_events), 4)
        self.assertTrue(any(event.event_type == "latest" for event in integration._system_events))

    async def test_unacknowledged_events_are_never_pruned(self) -> None:
        integration = AgentSystemIntegration(event_history_limit=2)
        for index in range(5):
            await integration.emit_system_event("pending", {"index": index})
        self.assertEqual(len(integration._system_events), 5)


if __name__ == "__main__":
    unittest.main()
