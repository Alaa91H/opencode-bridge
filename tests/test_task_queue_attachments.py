from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

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
            )
            self.assertEqual(position, 1)
            self.assertEqual(task.attachments[0]["filename"], "report.pdf")
            restored = await store.get(task.id)
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.attachments, task.attachments)
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
            await store.close()


if __name__ == "__main__":
    unittest.main()
