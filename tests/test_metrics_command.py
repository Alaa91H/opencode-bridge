"""Owner-facing `/metrics`: durable queue gauges + audit-derived outcomes."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from audit_log import AuditLogger, read_recent
from bridge.services.metrics_service import BridgeMetricsService, summarize_outcomes
from bridge.telegram.rendering.metrics import render_metrics


def _event(name: str, outcome: str, *, when: datetime, **details) -> dict:
    return {
        "timestamp": when.isoformat(),
        "event": name,
        "outcome": outcome,
        "actor_id": "1",
        "details": details,
    }


class AuditTailReaderTests(unittest.TestCase):
    def test_reads_bounded_tail_and_skips_broken_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path)
            for index in range(50):
                logger.write("task_finished", "success", details={"duration_seconds": index})
            with path.open("a", encoding="utf-8") as handle:
                handle.write("{not json\n")
                handle.write('{"event":"partial"')
            events = read_recent(path)
            self.assertTrue(events)
            self.assertTrue(all(isinstance(item, dict) for item in events))
            self.assertLessEqual(len(events), 2000)

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(read_recent(Path("does-not-exist.jsonl")), [])


class OutcomeSummaryTests(unittest.TestCase):
    def test_window_counts_durations_and_categories(self) -> None:
        now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        events = [
            _event("task_finished", "success", when=now - timedelta(minutes=5),
                   duration_seconds=10.0),
            _event("task_finished", "success", when=now - timedelta(minutes=9),
                   duration_seconds=30.0),
            _event("task_finished", "error", when=now - timedelta(minutes=7),
                   error_type="TimeoutError"),
            _event("task_deferred", "zen_free_quota", when=now - timedelta(minutes=6)),
            _event("task_deferred", "zen_free_quota", when=now - timedelta(minutes=3)),
            _event("task_deferred", "timeout", when=now - timedelta(minutes=2)),
            # Outside the window and irrelevant kinds must not be counted.
            _event("task_finished", "success", when=now - timedelta(days=3),
                   duration_seconds=99.0),
            _event("task_started", "accepted", when=now),
        ]
        summary = summarize_outcomes(events, since=now - timedelta(hours=24))
        self.assertEqual(summary.finished, 3)
        self.assertEqual(summary.succeeded, 2)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.deferred, 3)
        self.assertEqual(summary.avg_duration_seconds, 20.0)
        self.assertEqual(summary.max_duration_seconds, 30.0)
        self.assertEqual(
            summary.deferral_categories, (("zen_free_quota", 2), ("timeout", 1))
        )

    def test_empty_window_reports_none_durations(self) -> None:
        now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        summary = summarize_outcomes([], since=now)
        self.assertIsNone(summary.avg_duration_seconds)
        self.assertIsNone(summary.max_duration_seconds)
        self.assertEqual(summary.deferral_categories, ())

    def test_naive_timestamps_and_junk_are_tolerated(self) -> None:
        now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        events = [
            {"event": "task_finished", "outcome": "success", "timestamp": "not-a-time",
             "details": {}},
            {"event": "task_finished", "outcome": "success",
             "timestamp": now.replace(tzinfo=None).isoformat(), "details": {}},
            {"event": "task_finished", "outcome": "success", "timestamp": now.isoformat(),
             "details": {"duration_seconds": "not-a-number"}},
        ]
        summary = summarize_outcomes(events, since=now - timedelta(hours=1))
        self.assertEqual(summary.succeeded, 3)
        self.assertIsNone(summary.avg_duration_seconds)


class QueueGaugeTests(unittest.TestCase):
    def test_status_vocabulary_maps_to_gauges(self) -> None:
        service = BridgeMetricsService(None, audit_path=Path("unused.jsonl"))
        queue = service.queue_metrics({
            "queued": 3, "retrying": 2, "scheduled": 1, "running": 1, "leased": 1,
            "failed": 4, "dead_letter": 5, "completed": 6, "cancelled": 7,
        })
        self.assertEqual(queue.depth, 5)
        self.assertEqual(queue.active, 2)
        self.assertEqual(queue.failed, 9)
        self.assertEqual(queue.completed, 6)
        self.assertEqual(queue.cancelled, 7)
        self.assertEqual(queue.scheduled, 1)

    def test_missing_counts_default_to_zero(self) -> None:
        service = BridgeMetricsService(None, audit_path=Path("unused.jsonl"))
        queue = service.queue_metrics({})
        self.assertEqual(queue.depth, 0)
        self.assertEqual(queue.active, 0)


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _store(counts: dict[str, int]):
        class Store:
            async def queue_counts(self):
                return counts

        return Store()

    def _service(self, counts: dict[str, int], tmp: str, *, worker_limit=None):
        return BridgeMetricsService(
            self._store(counts),
            audit_path=Path(tmp) / "audit.jsonl",
            worker_limit=worker_limit,
            disk_path=Path(tmp),
        )

    async def test_snapshot_combines_store_and_audit(self) -> None:
        now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(_event(
                    "task_finished", "success", when=now, duration_seconds=12.0)) + "\n")
            service = self._service({"queued": 2, "running": 1}, tmp, worker_limit=lambda: 3)
            snapshot = await service.snapshot(now=now)
        self.assertEqual(snapshot.queue.depth, 2)
        self.assertEqual(snapshot.queue.active, 1)
        self.assertEqual(snapshot.worker_limit, 3)
        self.assertEqual(snapshot.outcomes.succeeded, 1)
        self.assertEqual(snapshot.outcomes.avg_duration_seconds, 12.0)
        self.assertEqual(snapshot.window_hours, 24)

    async def test_store_failure_degrades_instead_of_raising(self) -> None:
        class BrokenStore:
            async def queue_counts(self):
                raise RuntimeError("database is locked")

        with tempfile.TemporaryDirectory() as tmp:
            service = BridgeMetricsService(
                BrokenStore(), audit_path=Path(tmp) / "audit.jsonl")
            snapshot = await service.snapshot()
        self.assertEqual(snapshot.queue.depth, 0)
        self.assertEqual(snapshot.outcomes.finished, 0)

    async def test_window_argument_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service({}, tmp)
            self.assertEqual((await service.snapshot(window_hours=0)).window_hours, 1)
            self.assertEqual(
                (await service.snapshot(window_hours=99999)).window_hours, 24 * 30
            )


class RenderTests(unittest.TestCase):
    def _snapshot(self):
        return BridgeMetricsService(
            None, audit_path=Path("unused.jsonl")
        ).queue_metrics({"queued": 2, "retrying": 1, "running": 1})

    def test_render_contains_queue_outcomes_and_units(self) -> None:
        snapshot = SimpleNamespace(
            queue=self._snapshot(),
            outcomes=SimpleNamespace(
                finished=4, succeeded=3, failed=1, deferred=2,
                avg_duration_seconds=8.25, max_duration_seconds=20.0,
                deferral_categories=(("zen_free_quota", 2),),
            ),
            resources={"memory_bytes": 8 * 1024 ** 3, "cpu_percent": 12.5,
                       "disk_free_bytes": 40 * 1024 ** 3},
            worker_limit=2,
            window_hours=24,
            sampled_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        )
        text = render_metrics(snapshot)
        for expected in ("3", "8.2s", "20.0s", "8.0 GiB", "12.5%", "40.0 GiB",
                         "حصة Zen المجانية", "2026-10-17 12:00:00"):
            self.assertIn(expected, text)

    def test_render_without_history_or_resources(self) -> None:
        snapshot = SimpleNamespace(
            queue=self._snapshot(),
            outcomes=SimpleNamespace(
                finished=0, succeeded=0, failed=0, deferred=0,
                avg_duration_seconds=None, max_duration_seconds=None,
                deferral_categories=(),
            ),
            resources={},
            worker_limit=None,
            window_hours=6,
            sampled_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        )
        text = render_metrics(snapshot)
        self.assertIn("لا توجد مهام ناجحة", text)
        self.assertNotIn("GiB", text)


class WindowArgumentTests(unittest.TestCase):
    def test_argument_is_clamped_or_ignored(self) -> None:
        from bridge.telegram.commands.metrics import parse_window_hours

        self.assertEqual(parse_window_hours("48"), 48)
        self.assertEqual(parse_window_hours("48 extra"), 48)
        self.assertEqual(parse_window_hours("0"), 1)
        self.assertEqual(parse_window_hours("-5"), 1)
        self.assertEqual(parse_window_hours("9999"), 24 * 30)
        self.assertIsNone(parse_window_hours("abc"))
        self.assertIsNone(parse_window_hours(""))
        self.assertIsNone(parse_window_hours(None))


class MetricsCommandTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(self, service, replies):
        from bridge.telegram.commands.metrics import MetricsCommands

        async def reply(message, text):
            replies.append(text)

        return MetricsCommands(service, reply=reply, error_message=lambda exc, op: "خطأ")

    def _real_service(self, tmp: str, seen: dict):
        class Store:
            async def queue_counts(self):
                return {}

        service = BridgeMetricsService(Store(), audit_path=Path(tmp) / "audit.jsonl")
        inner = service.snapshot

        async def snapshot(**kwargs):
            seen.update(kwargs)
            return await inner(**kwargs)

        service.snapshot = snapshot
        return service

    async def test_argument_window_is_forwarded_and_rendered(self) -> None:
        seen: dict = {}
        replies: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._adapter(self._real_service(tmp, seen), replies)
            await adapter.metrics(
                SimpleNamespace(message=SimpleNamespace(text="/metrics 48")),
                SimpleNamespace(args=["48"]),
            )
        self.assertEqual(seen.get("window_hours"), 48)
        self.assertTrue(replies)
        self.assertIn("قياسات الجسر", replies[0])

    async def test_panel_dispatch_uses_args_not_the_callback_message(self) -> None:
        seen: dict = {}
        replies: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._adapter(self._real_service(tmp, seen), replies)
            await adapter.metrics(
                SimpleNamespace(message=SimpleNamespace(text="⚙️ قياس")),
                SimpleNamespace(args=["72"]),
            )
        self.assertEqual(seen.get("window_hours"), 72)

    async def test_foreign_message_text_is_never_parsed_as_a_window(self) -> None:
        seen: dict = {}
        replies: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._adapter(self._real_service(tmp, seen), replies)
            await adapter.metrics(
                SimpleNamespace(message=SimpleNamespace(text="48gb free")),
                SimpleNamespace(args=[]),
            )
        self.assertNotIn("window_hours", seen)

    async def test_no_argument_keeps_the_service_default(self) -> None:
        seen: dict = {}
        replies: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._adapter(self._real_service(tmp, seen), replies)
            await adapter.metrics(
                SimpleNamespace(message=SimpleNamespace(text="/metrics")),
                SimpleNamespace(args=[]),
            )
        self.assertNotIn("window_hours", seen)
        self.assertIn("24", replies[0])

    async def test_unusable_argument_keeps_the_service_default(self) -> None:
        seen: dict = {}
        replies: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._adapter(self._real_service(tmp, seen), replies)
            await adapter.metrics(
                SimpleNamespace(message=SimpleNamespace(text="/metrics soon")),
                SimpleNamespace(args=["soon"]),
            )
        self.assertNotIn("window_hours", seen)
        self.assertTrue(replies)

    async def test_service_failure_replies_with_error_text(self) -> None:
        class Broken:
            async def snapshot(self, **kwargs):
                raise RuntimeError("nope")

        replies: list[str] = []
        await self._adapter(Broken(), replies).metrics(
            SimpleNamespace(message=SimpleNamespace(text="/metrics")), SimpleNamespace()
        )
        self.assertEqual(replies, ["خطأ"])

    async def test_failure_without_translator_still_replies(self) -> None:
        from bridge.telegram.commands.metrics import MetricsCommands

        class Broken:
            async def snapshot(self, **kwargs):
                raise RuntimeError("nope")

        replies: list[str] = []

        async def reply(message, text):
            replies.append(text)

        adapter = MetricsCommands(Broken(), reply=reply)
        await adapter.metrics(
            SimpleNamespace(message=SimpleNamespace(text="/metrics")), SimpleNamespace()
        )
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0])

    async def test_missing_message_is_ignored(self) -> None:
        replies: list[str] = []
        await self._adapter(SimpleNamespace(), replies).metrics(
            SimpleNamespace(message=None), SimpleNamespace()
        )
        self.assertEqual(replies, [])


if __name__ == "__main__":
    unittest.main()
