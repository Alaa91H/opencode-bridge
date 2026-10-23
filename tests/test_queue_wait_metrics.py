"""Queue wait-latency metrics derived from durable task timestamps."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.services.metrics_service import BridgeMetricsService
from bridge.telegram.rendering.metrics import render_metrics
from task_queue import TaskQueueStore


class QueueWaitSamplesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = BridgeDatabase(Path(self._tmp.name) / "bridge.db")
        self.store = TaskQueueStore(
            Path(self._tmp.name) / "bridge.db", database=self.database
        )
        await self.store.init()

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def _insert(self, created: datetime, started: datetime | None) -> None:
        db = await self.database.connect()
        await db.execute(
            """INSERT INTO agent_tasks
               (owner_id, chat_id, prompt, status, created_at, updated_at, started_at)
               VALUES ('1', 2, 'p', 'running', ?, ?, ?)""",
            (
                created.isoformat(),
                created.isoformat(),
                started.isoformat() if started else None,
            ),
        )
        await db.commit()

    async def test_samples_are_positive_and_ordered_by_recency(self) -> None:
        now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
        await self._insert(now - timedelta(minutes=10), now - timedelta(minutes=10) + timedelta(seconds=5))
        await self._insert(now - timedelta(minutes=5), now - timedelta(minutes=5) + timedelta(seconds=1))
        await self._insert(now - timedelta(minutes=2), None)
        samples = await self.store.queue_wait_seconds(limit=10)
        self.assertEqual(samples, (1.0, 5.0))

    async def test_window_excludes_old_tasks(self) -> None:
        now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
        await self._insert(now - timedelta(days=3), now - timedelta(days=3) + timedelta(seconds=9))
        await self._insert(now - timedelta(minutes=2), now - timedelta(minutes=2) + timedelta(seconds=2))
        samples = await self.store.queue_wait_seconds(
            limit=10, since=now - timedelta(hours=1)
        )
        self.assertEqual(samples, (2.0,))


class WaitMetricsRenderingTests(unittest.TestCase):
    def test_wait_percentiles_are_reported(self) -> None:
        service = BridgeMetricsService(None, audit_path=Path("unused.jsonl"))
        queue = service.queue_metrics({}, (1.0, 2.0, 3.0, 40.0))
        self.assertEqual(queue.wait_p50_seconds, 2.0)
        self.assertEqual(queue.wait_p95_seconds, 40.0)
        self.assertEqual(queue.wait_samples, 4)

    def test_render_includes_wait_line_only_when_sampled(self) -> None:
        service = BridgeMetricsService(None, audit_path=Path("unused.jsonl"))
        queue = service.queue_metrics({}, (2.0, 4.0))
        snapshot = SimpleNamespace(
            queue=queue,
            outcomes=SimpleNamespace(
                finished=0, succeeded=0, failed=0, deferred=0,
                avg_duration_seconds=None, max_duration_seconds=None,
                deferral_categories=(),
            ),
            resources={},
            worker_limit=None,
            window_hours=24,
            sampled_at=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
        )
        text = render_metrics(snapshot)
        self.assertIn("p50 2.0s", text)
        self.assertIn("p95 4.0s", text)
        self.assertIn("2 مهمة", text)

        empty = service.queue_metrics({}, ())
        snapshot.queue = empty
        self.assertNotIn("انتظار قبل البدء", render_metrics(snapshot))


if __name__ == "__main__":
    unittest.main()
