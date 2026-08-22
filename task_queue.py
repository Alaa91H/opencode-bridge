"""Persistent queue and scheduler storage for Telegram agent tasks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def encode_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def decode_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def encode_attachments(attachments: list[dict[str, Any]] | None) -> str:
    return json.dumps(attachments or [], ensure_ascii=False, separators=(",", ":"))


def decode_attachments(value: str | None) -> tuple[dict[str, Any], ...]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, dict))


@dataclass(frozen=True)
class QueuedTask:
    id: int
    owner_id: str
    chat_id: int
    prompt: str
    status: str
    created_at: datetime
    due_at: datetime | None
    repeat_seconds: int | None
    sequence: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None
    attachments: tuple[dict[str, Any], ...] = ()

    @property
    def is_recurring(self) -> bool:
        return bool(self.repeat_seconds)


class TaskQueueStore:
    """SQLite-backed task queue. All timestamps are stored in UTC."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(str(self.db_path))
            self._db.row_factory = aiosqlite.Row
        return self._db

    async def init(self) -> None:
        db = await self._get_db()
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_tasks (
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
                last_error TEXT,
                attachments_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        async with db.execute("PRAGMA table_info(agent_tasks)") as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        if "attachments_json" not in columns:
            await db.execute("ALTER TABLE agent_tasks ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_status_sequence "
            "ON agent_tasks(owner_id, status, sequence, id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_due "
            "ON agent_tasks(status, due_at)"
        )
        await db.commit()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> QueuedTask:
        attachment_value = row["attachments_json"] if "attachments_json" in row.keys() else "[]"
        return QueuedTask(
            id=int(row["id"]),
            owner_id=str(row["owner_id"]),
            chat_id=int(row["chat_id"]),
            prompt=str(row["prompt"]),
            status=str(row["status"]),
            created_at=decode_time(row["created_at"]) or utc_now(),
            due_at=decode_time(row["due_at"]),
            repeat_seconds=row["repeat_seconds"],
            sequence=int(row["sequence"]),
            started_at=decode_time(row["started_at"]),
            completed_at=decode_time(row["completed_at"]),
            last_error=row["last_error"],
            attachments=decode_attachments(attachment_value),
        )

    async def enqueue(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> tuple[QueuedTask, int]:
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM agent_tasks WHERE owner_id = ? AND status IN ('queued', 'running')",
                (owner_id,),
            ) as cursor:
                sequence = int((await cursor.fetchone())["next_sequence"])
            cursor = await db.execute(
                """
                INSERT INTO agent_tasks
                (owner_id, chat_id, prompt, status, created_at, updated_at, sequence, attachments_json)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (owner_id, chat_id, prompt, now, now, sequence, encode_attachments(attachments)),
            )
            task_id = int(cursor.lastrowid)
            await db.commit()
        task = await self.get(task_id)
        assert task is not None
        return task, sequence

    async def schedule(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        due_at: datetime,
        repeat_seconds: int | None = None,
    ) -> QueuedTask:
        if due_at <= utc_now():
            raise ValueError("وقت التنفيذ لازم يكون بالمستقبل")
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                INSERT INTO agent_tasks
                (owner_id, chat_id, prompt, status, created_at, updated_at, due_at, repeat_seconds, sequence)
                VALUES (?, ?, ?, 'scheduled', ?, ?, ?, ?, 0)
                """,
                (owner_id, chat_id, prompt, now, now, encode_time(due_at), repeat_seconds),
            )
            task_id = int(cursor.lastrowid)
            await db.commit()
        task = await self.get(task_id)
        assert task is not None
        return task

    async def get(self, task_id: int) -> QueuedTask | None:
        db = await self._get_db()
        async with self._lock:
            async with db.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)) as cursor:
                row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def list_active(self, owner_id: str, limit: int = 20) -> list[QueuedTask]:
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                """
                SELECT * FROM agent_tasks
                WHERE owner_id = ? AND status IN ('queued', 'scheduled', 'running')
                ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                         CASE WHEN status = 'scheduled' THEN due_at ELSE created_at END,
                         sequence, id
                LIMIT ?
                """,
                (owner_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]

    async def cancel(self, task_id: int, owner_id: str) -> QueuedTask | None:
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                UPDATE agent_tasks SET status = 'cancelled', updated_at = ?, completed_at = ?
                WHERE id = ? AND owner_id = ? AND status IN ('queued', 'scheduled', 'running')
                """,
                (now, now, task_id, owner_id),
            )
            await db.commit()
        return await self.get(task_id) if cursor.rowcount else None

    async def cancel_running_for_owner(self, owner_id: str) -> QueuedTask | None:
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                "SELECT id FROM agent_tasks WHERE owner_id = ? AND status = 'running' ORDER BY started_at DESC LIMIT 1",
                (owner_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return await self.cancel(int(row["id"]), owner_id) if row else None

    async def promote_due(self) -> int:
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                UPDATE agent_tasks SET status = 'queued', updated_at = ?, sequence = 0
                WHERE status = 'scheduled' AND due_at <= ?
                """,
                (now, now),
            )
            await db.commit()
        return cursor.rowcount

    async def claim_next(self) -> QueuedTask | None:
        """Claim one queued task if its owner has no other running task."""
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT q.* FROM agent_tasks q
                    WHERE q.status = 'queued'
                      AND NOT EXISTS (
                          SELECT 1 FROM agent_tasks r
                          WHERE r.owner_id = q.owner_id AND r.status = 'running'
                      )
                    ORDER BY q.created_at, q.id
                    LIMIT 1
                    """
                ) as cursor:
                    row = await cursor.fetchone()
                if not row:
                    await db.commit()
                    return None
                task_id = int(row["id"])
                await db.execute(
                    "UPDATE agent_tasks SET status = 'running', started_at = ?, updated_at = ? WHERE id = ? AND status = 'queued'",
                    (now, now, task_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return await self.get(task_id)

    async def finish(self, task_id: int, success: bool, error: str | None = None) -> QueuedTask | None:
        task = await self.get(task_id)
        if not task or task.status == "cancelled":
            return task
        now_dt = utc_now()
        now = encode_time(now_dt)
        db = await self._get_db()
        async with self._lock:
            if task.repeat_seconds:
                next_due = task.due_at or now_dt
                while next_due <= now_dt:
                    next_due += timedelta(seconds=task.repeat_seconds)
                await db.execute(
                    """
                    UPDATE agent_tasks
                    SET status = 'scheduled', due_at = ?, updated_at = ?, started_at = NULL,
                        completed_at = ?, last_error = ?
                    WHERE id = ?
                    """,
                    (encode_time(next_due), now, now, None if success else error, task_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE agent_tasks
                    SET status = ?, updated_at = ?, completed_at = ?, last_error = ?
                    WHERE id = ?
                    """,
                    ("completed" if success else "failed", now, now, error, task_id),
                )
            await db.commit()
        return await self.get(task_id)

    async def recover_interrupted(self) -> int:
        """Mark work interrupted by a process restart as failed; never replay it silently."""
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                UPDATE agent_tasks
                SET status = 'failed', completed_at = ?, updated_at = ?,
                    last_error = 'انقطع تشغيل البوت قبل اكتمال المهمة'
                WHERE status = 'running'
                """,
                (now, now),
            )
            await db.commit()
        return cursor.rowcount

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
