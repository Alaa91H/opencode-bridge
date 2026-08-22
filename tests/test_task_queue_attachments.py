from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from task_queue import TaskQueueStore


class TaskQueueAttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_persists_attachment_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskQueueStore(Path(temp) / "queue.db")
            await store.init()
            task, position = await store.enqueue(
                "7",
                9,
                "حلل المرفق",
                attachments=[
                    {
                        "path": "/managed/incoming/7/report.pdf",
                        "filename": "report.pdf",
                        "mime": "application/pdf",
                        "size": 12,
                        "kind": "document",
                    }
                ],
                execution_mode="deepresearch",
            )
            self.assertEqual(position, 1)
            self.assertEqual(task.attachments[0]["filename"], "report.pdf")
            self.assertEqual(task.execution_mode, "deepresearch")
            restored = await store.get(task.id)
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.attachments, task.attachments)
            self.assertEqual(restored.execution_mode, "deepresearch")
            await store.close()

    async def test_daily_counter_resets_at_local_midnight_without_deleting_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = TaskQueueStore(Path(temp) / "queue.db")
            await store.init()
            day_timezone = ZoneInfo("Etc/GMT-2")
            before_midnight, _ = await store.enqueue(
                "7",
                9,
                "مهمة قبل منتصف الليل",
                created_at=datetime(2026, 8, 22, 21, 59, tzinfo=timezone.utc),
            )
            at_midnight, _ = await store.enqueue(
                "7",
                9,
                "مهمة بعد منتصف الليل",
                created_at=datetime(2026, 8, 22, 22, 0, tzinfo=timezone.utc),
            )
            previous_day_count = await store.count_created_for_day(
                "7",
                reference_at=datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
                day_timezone=day_timezone,
            )
            next_day_count = await store.count_created_for_day(
                "7",
                reference_at=datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
                day_timezone=day_timezone,
            )
            self.assertEqual(previous_day_count, 1)
            self.assertEqual(next_day_count, 1)
            self.assertLess(before_midnight.id, at_midnight.id)
            self.assertIsNotNone(await store.get(before_midnight.id))
            self.assertIsNotNone(await store.get(at_midnight.id))
            await store.close()

    async def test_init_migrates_existing_queue_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "legacy.db"
            with sqlite3.connect(database) as conn:
                conn.execute(
                    """
                    CREATE TABLE agent_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id TEXT NOT NULL,
                        chat_id INTEGER NOT NULL,
                        prompt TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        due_at TEXT,
                        repeat_seconds INTEGER,
                        sequence INTEGER NOT NULL DEFAULT 0,
                        started_at TEXT,
                        completed_at TEXT,
                        last_error TEXT
                    )
                    """
                )
            store = TaskQueueStore(database)
            await store.init()
            task, _ = await store.enqueue("7", 9, "مهمة قديمة البنية")
            self.assertEqual(task.attachments, ())
            await store.update_activity(
                task.id,
                [{"time": "2026-08-22T14:00:00+00:00", "phase": "started", "message": "بدأت المهمة", "kind": "info"}],
            )
            restored = await store.get(task.id)
            assert restored is not None
            self.assertEqual(restored.activity[0]["phase"], "started")
            self.assertEqual(restored.activity[0]["message"], "بدأت المهمة")
            await store.close()


if __name__ == "__main__":
    unittest.main()
