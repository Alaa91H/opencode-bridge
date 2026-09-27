"""Persistent queue and scheduler storage for Telegram agent tasks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
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


def encode_activity(activity: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None) -> str:
    return json.dumps(list(activity or ()), ensure_ascii=False, separators=(",", ":"))


def decode_activity(value: str | None) -> tuple[dict[str, Any], ...]:
    return decode_attachments(value)


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
    activity: tuple[dict[str, Any], ...] = ()
    execution_mode: str | None = None
    status_message_id: int | None = None
    schedule_job_id: int | None = None

    @property
    def is_recurring(self) -> bool:
        return bool(self.repeat_seconds)


@dataclass(frozen=True)
class ScheduledJob:
    id: int
    owner_id: str
    chat_id: int
    name: str
    prompt: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None
    repeat_seconds: int | None
    timezone_name: str
    last_run_at: datetime | None = None
    last_error: str | None = None

    @property
    def is_recurring(self) -> bool:
        return bool(self.repeat_seconds)


@dataclass(frozen=True)
class PendingAttachmentBatch:
    owner_id: str
    chat_id: int
    attachments: tuple[dict[str, Any], ...]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


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
                attachments_json TEXT NOT NULL DEFAULT '[]',
                activity_json TEXT NOT NULL DEFAULT '[]',
                execution_mode TEXT,
                status_message_id INTEGER,
                schedule_job_id INTEGER
            )
            """
        )
        async with db.execute("PRAGMA table_info(agent_tasks)") as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        if "attachments_json" not in columns:
            await db.execute("ALTER TABLE agent_tasks ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'")
        if "activity_json" not in columns:
            await db.execute("ALTER TABLE agent_tasks ADD COLUMN activity_json TEXT NOT NULL DEFAULT '[]'")
        if "execution_mode" not in columns:
            await db.execute("ALTER TABLE agent_tasks ADD COLUMN execution_mode TEXT")
        if "status_message_id" not in columns:
            await db.execute("ALTER TABLE agent_tasks ADD COLUMN status_message_id INTEGER")
        if "schedule_job_id" not in columns:
            await db.execute("ALTER TABLE agent_tasks ADD COLUMN schedule_job_id INTEGER")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_status_sequence "
            "ON agent_tasks(owner_id, status, sequence, id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_due "
            "ON agent_tasks(status, due_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_created "
            "ON agent_tasks(owner_id, created_at)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                prompt TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_run_at TEXT,
                repeat_seconds INTEGER,
                timezone_name TEXT NOT NULL DEFAULT 'UTC',
                last_run_at TEXT,
                last_error TEXT,
                UNIQUE(owner_id, name)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_owner_name "
            "ON scheduled_jobs(owner_id, name COLLATE NOCASE)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due "
            "ON scheduled_jobs(enabled, next_run_at)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_attachment_batches (
                owner_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                attachments_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (owner_id, chat_id)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_attachment_expiry "
            "ON pending_attachment_batches(expires_at)"
        )
        await db.commit()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> QueuedTask:
        attachment_value = row["attachments_json"] if "attachments_json" in row.keys() else "[]"
        activity_value = row["activity_json"] if "activity_json" in row.keys() else "[]"
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
            activity=decode_activity(activity_value),
            execution_mode=str(row["execution_mode"]) if "execution_mode" in row.keys() and row["execution_mode"] else None,
            status_message_id=(
                int(row["status_message_id"])
                if "status_message_id" in row.keys() and row["status_message_id"] is not None
                else None
            ),
            schedule_job_id=(
                int(row["schedule_job_id"])
                if "schedule_job_id" in row.keys() and row["schedule_job_id"] is not None
                else None
            ),
        )

    async def update_activity(self, task_id: int, activity: list[dict[str, Any]]) -> None:
        """Persist a compact, already-sanitized activity trail for one task."""
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            await db.execute(
                "UPDATE agent_tasks SET activity_json = ?, updated_at = ? WHERE id = ?",
                (encode_activity(activity[-40:]), now, task_id),
            )
            await db.commit()

    @staticmethod
    def _scheduled_from_row(row: aiosqlite.Row) -> ScheduledJob:
        return ScheduledJob(
            id=int(row["id"]),
            owner_id=str(row["owner_id"]),
            chat_id=int(row["chat_id"]),
            name=str(row["name"]),
            prompt=str(row["prompt"]),
            enabled=bool(row["enabled"]),
            created_at=decode_time(row["created_at"]) or utc_now(),
            updated_at=decode_time(row["updated_at"]) or utc_now(),
            next_run_at=decode_time(row["next_run_at"]),
            repeat_seconds=int(row["repeat_seconds"]) if row["repeat_seconds"] is not None else None,
            timezone_name=str(row["timezone_name"] or "UTC"),
            last_run_at=decode_time(row["last_run_at"]),
            last_error=str(row["last_error"]) if row["last_error"] else None,
        )

    async def create_scheduled_job(
        self,
        owner_id: str,
        chat_id: int,
        name: str,
        prompt: str,
        next_run_at: datetime,
        repeat_seconds: int | None = None,
        timezone_name: str = "UTC",
    ) -> ScheduledJob:
        clean_name = name.strip()
        clean_prompt = prompt.strip()
        if not clean_name:
            raise ValueError("اسم الجدولة فارغ")
        if len(clean_name) > 80:
            raise ValueError("اسم الجدولة أطول من 80 محرفًا")
        if not clean_prompt:
            raise ValueError("أمر الجدولة فارغ")
        if next_run_at <= utc_now():
            raise ValueError("وقت التشغيل يجب أن يكون في المستقبل")
        if repeat_seconds is not None and int(repeat_seconds) < 300:
            raise ValueError("أقصر تكرار مسموح هو 5 دقائق")
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            try:
                cursor = await db.execute(
                    """
                    INSERT INTO scheduled_jobs
                    (owner_id, chat_id, name, prompt, enabled, created_at, updated_at,
                     next_run_at, repeat_seconds, timezone_name)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner_id,
                        chat_id,
                        clean_name,
                        clean_prompt,
                        now,
                        now,
                        encode_time(next_run_at),
                        int(repeat_seconds) if repeat_seconds is not None else None,
                        timezone_name or "UTC",
                    ),
                )
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                raise ValueError("يوجد بالفعل جدول بهذا الاسم") from exc
        job = await self.get_scheduled_job_by_id(int(cursor.lastrowid), owner_id)
        assert job is not None
        return job

    async def get_scheduled_job_by_id(self, job_id: int, owner_id: str | None = None) -> ScheduledJob | None:
        db = await self._get_db()
        async with self._lock:
            if owner_id is None:
                query, params = "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
            else:
                query, params = "SELECT * FROM scheduled_jobs WHERE id = ? AND owner_id = ?", (job_id, owner_id)
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
        return self._scheduled_from_row(row) if row else None

    async def get_scheduled_job(self, owner_id: str, name: str) -> ScheduledJob | None:
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                "SELECT * FROM scheduled_jobs WHERE owner_id = ? AND name = ? COLLATE NOCASE",
                (owner_id, name.strip()),
            ) as cursor:
                row = await cursor.fetchone()
        return self._scheduled_from_row(row) if row else None

    async def list_scheduled_jobs(self, owner_id: str, limit: int = 100) -> list[ScheduledJob]:
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE owner_id = ?
                ORDER BY enabled DESC,
                         CASE WHEN next_run_at IS NULL THEN 1 ELSE 0 END,
                         next_run_at,
                         name COLLATE NOCASE
                LIMIT ?
                """,
                (owner_id, max(1, min(int(limit), 500))),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._scheduled_from_row(row) for row in rows]

    async def rename_scheduled_job(self, owner_id: str, name: str, new_name: str) -> ScheduledJob | None:
        clean_name = new_name.strip()
        if not clean_name or len(clean_name) > 80:
            raise ValueError("الاسم الجديد يجب أن يكون بين 1 و80 محرفًا")
        db = await self._get_db()
        async with self._lock:
            try:
                cursor = await db.execute(
                    """
                    UPDATE scheduled_jobs SET name = ?, updated_at = ?
                    WHERE owner_id = ? AND name = ? COLLATE NOCASE
                    """,
                    (clean_name, encode_time(utc_now()), owner_id, name.strip()),
                )
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                raise ValueError("يوجد بالفعل جدول بهذا الاسم") from exc
        return await self.get_scheduled_job(owner_id, clean_name) if cursor.rowcount else None

    async def set_scheduled_job_prompt(self, owner_id: str, name: str, prompt: str) -> ScheduledJob | None:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("أمر الجدولة فارغ")
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                UPDATE scheduled_jobs SET prompt = ?, updated_at = ?
                WHERE owner_id = ? AND name = ? COLLATE NOCASE
                """,
                (clean_prompt, encode_time(utc_now()), owner_id, name.strip()),
            )
            await db.commit()
        return await self.get_scheduled_job(owner_id, name) if cursor.rowcount else None

    async def append_scheduled_job_prompt(self, owner_id: str, name: str, text: str) -> ScheduledJob | None:
        addition = text.rstrip()
        if not addition:
            raise ValueError("النص المراد إلحاقه فارغ")
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                UPDATE scheduled_jobs
                SET prompt = CASE WHEN prompt = '' THEN ? ELSE prompt || char(10) || ? END,
                    updated_at = ?
                WHERE owner_id = ? AND name = ? COLLATE NOCASE
                """,
                (addition, addition, encode_time(utc_now()), owner_id, name.strip()),
            )
            await db.commit()
        return await self.get_scheduled_job(owner_id, name) if cursor.rowcount else None

    async def update_scheduled_job_timing(
        self,
        owner_id: str,
        name: str,
        next_run_at: datetime,
        repeat_seconds: int | None,
        timezone_name: str = "UTC",
    ) -> ScheduledJob | None:
        if next_run_at <= utc_now():
            raise ValueError("وقت التشغيل يجب أن يكون في المستقبل")
        if repeat_seconds is not None and int(repeat_seconds) < 300:
            raise ValueError("أقصر تكرار مسموح هو 5 دقائق")
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                UPDATE scheduled_jobs
                SET next_run_at = ?, repeat_seconds = ?, timezone_name = ?,
                    updated_at = ?, last_error = NULL
                WHERE owner_id = ? AND name = ? COLLATE NOCASE
                """,
                (
                    encode_time(next_run_at),
                    int(repeat_seconds) if repeat_seconds is not None else None,
                    timezone_name or "UTC",
                    encode_time(utc_now()),
                    owner_id,
                    name.strip(),
                ),
            )
            await db.commit()
        return await self.get_scheduled_job(owner_id, name) if cursor.rowcount else None

    async def set_scheduled_job_enabled(self, owner_id: str, name: str, enabled: bool) -> ScheduledJob | None:
        job = await self.get_scheduled_job(owner_id, name)
        if job is None:
            return None
        next_run_at = job.next_run_at
        if enabled and (next_run_at is None or next_run_at <= utc_now()):
            if job.repeat_seconds:
                next_run_at = utc_now() + timedelta(seconds=job.repeat_seconds)
            else:
                next_run_at = utc_now() + timedelta(seconds=1)
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                UPDATE scheduled_jobs
                SET enabled = ?, next_run_at = ?, updated_at = ?, last_error = NULL
                WHERE id = ? AND owner_id = ?
                """,
                (
                    1 if enabled else 0,
                    encode_time(next_run_at) if next_run_at else None,
                    encode_time(utc_now()),
                    job.id,
                    owner_id,
                ),
            )
            await db.commit()
        return await self.get_scheduled_job_by_id(job.id, owner_id) if cursor.rowcount else None

    async def delete_scheduled_job(self, owner_id: str, name: str) -> bool:
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                "DELETE FROM scheduled_jobs WHERE owner_id = ? AND name = ? COLLATE NOCASE",
                (owner_id, name.strip()),
            )
            await db.commit()
        return bool(cursor.rowcount)

    async def enqueue_scheduled_job_now(
        self,
        owner_id: str,
        name: str,
        status_message_id: int | None = None,
    ) -> QueuedTask | None:
        job = await self.get_scheduled_job(owner_id, name)
        if job is None:
            return None
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM agent_tasks "
                "WHERE owner_id = ? AND status IN ('queued', 'running')",
                (owner_id,),
            ) as cursor:
                sequence = int((await cursor.fetchone())["next_sequence"])
            cursor = await db.execute(
                """
                INSERT INTO agent_tasks
                (owner_id, chat_id, prompt, status, created_at, updated_at, sequence,
                 attachments_json, execution_mode, status_message_id, schedule_job_id)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, '[]', NULL, ?, ?)
                """,
                (
                    job.owner_id,
                    job.chat_id,
                    job.prompt,
                    now,
                    now,
                    sequence,
                    status_message_id,
                    job.id,
                ),
            )
            await db.commit()
        return await self.get(int(cursor.lastrowid))

    @staticmethod
    def _pending_from_row(row: aiosqlite.Row) -> PendingAttachmentBatch:
        return PendingAttachmentBatch(
            owner_id=str(row["owner_id"]),
            chat_id=int(row["chat_id"]),
            attachments=decode_attachments(row["attachments_json"]),
            created_at=decode_time(row["created_at"]) or utc_now(),
            updated_at=decode_time(row["updated_at"]) or utc_now(),
            expires_at=decode_time(row["expires_at"]) or utc_now(),
        )

    async def stage_pending_attachments(
        self,
        owner_id: str,
        chat_id: int,
        attachments: list[dict[str, Any]],
        ttl_seconds: int,
        max_count: int,
        max_total_bytes: int,
    ) -> tuple[PendingAttachmentBatch, tuple[dict[str, Any], ...]]:
        """Persist files waiting for the user's next instruction.

        Returns the active batch plus any expired records it replaced so the
        caller can remove their managed files safely.
        """
        if not attachments:
            raise ValueError("لا توجد مرفقات لحفظها")
        now_dt = utc_now()
        now = encode_time(now_dt)
        expires_at = now_dt + timedelta(seconds=max(60, int(ttl_seconds)))
        db = await self._get_db()
        expired_records: tuple[dict[str, Any], ...] = ()
        async with self._lock:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT * FROM pending_attachment_batches WHERE owner_id = ? AND chat_id = ?",
                    (owner_id, chat_id),
                ) as cursor:
                    row = await cursor.fetchone()
                existing: list[dict[str, Any]] = []
                created_at = now
                if row is not None:
                    previous = self._pending_from_row(row)
                    if previous.expires_at <= now_dt:
                        expired_records = previous.attachments
                    else:
                        existing = list(previous.attachments)
                        created_at = encode_time(previous.created_at)

                merged: list[dict[str, Any]] = []
                seen_paths: set[str] = set()
                for record in [*existing, *attachments]:
                    path = str(record.get("path", ""))
                    if path and path in seen_paths:
                        continue
                    if path:
                        seen_paths.add(path)
                    merged.append(record)

                if len(merged) > max(1, int(max_count)):
                    raise ValueError(f"عدد المرفقات أكبر من الحد المسموح ({max_count})")
                total_size = 0
                for record in merged:
                    try:
                        size = int(record.get("size", 0))
                    except (TypeError, ValueError) as exc:
                        raise ValueError("حجم أحد المرفقات غير صالح") from exc
                    if size < 0:
                        raise ValueError("حجم أحد المرفقات غير صالح")
                    total_size += size
                if total_size > max(1, int(max_total_bytes)):
                    raise ValueError(
                        f"الحجم الإجمالي للمرفقات يتجاوز الحد المسموح ({max_total_bytes // (1024 * 1024)} MiB)"
                    )

                await db.execute(
                    """
                    INSERT INTO pending_attachment_batches
                    (owner_id, chat_id, attachments_json, created_at, updated_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner_id, chat_id) DO UPDATE SET
                        attachments_json = excluded.attachments_json,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        owner_id,
                        chat_id,
                        encode_attachments(merged),
                        created_at,
                        now,
                        encode_time(expires_at),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        return (
            PendingAttachmentBatch(
                owner_id=owner_id,
                chat_id=chat_id,
                attachments=tuple(merged),
                created_at=decode_time(created_at) or now_dt,
                updated_at=now_dt,
                expires_at=expires_at,
            ),
            expired_records,
        )

    async def pop_pending_attachments(
        self,
        owner_id: str,
        chat_id: int,
    ) -> tuple[tuple[dict[str, Any], ...], bool]:
        """Atomically remove and return one pending batch and whether it expired."""
        now_dt = utc_now()
        db = await self._get_db()
        async with self._lock:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT * FROM pending_attachment_batches WHERE owner_id = ? AND chat_id = ?",
                    (owner_id, chat_id),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    await db.commit()
                    return (), False
                batch = self._pending_from_row(row)
                await db.execute(
                    "DELETE FROM pending_attachment_batches WHERE owner_id = ? AND chat_id = ?",
                    (owner_id, chat_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return batch.attachments, batch.expires_at <= now_dt

    async def purge_expired_pending_attachments(self) -> tuple[dict[str, Any], ...]:
        """Delete expired pending rows and return their records for managed file cleanup."""
        now = encode_time(utc_now())
        db = await self._get_db()
        records: list[dict[str, Any]] = []
        async with self._lock:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT attachments_json FROM pending_attachment_batches WHERE expires_at <= ?",
                    (now,),
                ) as cursor:
                    rows = await cursor.fetchall()
                for row in rows:
                    records.extend(decode_attachments(row["attachments_json"]))
                await db.execute(
                    "DELETE FROM pending_attachment_batches WHERE expires_at <= ?",
                    (now,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return tuple(records)

    async def enqueue(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        attachments: list[dict[str, Any]] | None = None,
        created_at: datetime | None = None,
        execution_mode: str | None = None,
        status_message_id: int | None = None,
    ) -> tuple[QueuedTask, int]:
        now = encode_time(created_at or utc_now())
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
                (owner_id, chat_id, prompt, status, created_at, updated_at, sequence, attachments_json, execution_mode, status_message_id)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_id,
                    chat_id,
                    prompt,
                    now,
                    now,
                    sequence,
                    encode_attachments(attachments),
                    execution_mode,
                    status_message_id,
                ),
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
        status_message_id: int | None = None,
    ) -> QueuedTask:
        if due_at <= utc_now():
            raise ValueError("وقت التنفيذ لازم يكون بالمستقبل")
        now = encode_time(utc_now())
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                """
                INSERT INTO agent_tasks
                (owner_id, chat_id, prompt, status, created_at, updated_at, due_at, repeat_seconds, sequence, status_message_id)
                VALUES (?, ?, ?, 'scheduled', ?, ?, ?, ?, 0, ?)
                """,
                (owner_id, chat_id, prompt, now, now, encode_time(due_at), repeat_seconds, status_message_id),
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

    async def set_status_message_id(self, task_id: int, message_id: int | None) -> None:
        db = await self._get_db()
        async with self._lock:
            await db.execute(
                "UPDATE agent_tasks SET status_message_id = ?, updated_at = ? WHERE id = ?",
                (message_id, encode_time(utc_now()), task_id),
            )
            await db.commit()

    async def latest_active_for_owner(self, owner_id: str) -> QueuedTask | None:
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                """
                SELECT * FROM agent_tasks
                WHERE owner_id = ? AND status IN ('running', 'queued', 'scheduled')
                ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                         CASE WHEN status = 'scheduled' THEN due_at ELSE created_at END,
                         sequence, id
                LIMIT 1
                """,
                (owner_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def cancel_latest_for_owner(self, owner_id: str) -> QueuedTask | None:
        task = await self.latest_active_for_owner(owner_id)
        if task is None:
            return None
        return await self.cancel(task.id, owner_id)

    async def count_created_for_day(
        self,
        owner_id: str,
        reference_at: datetime | None = None,
        day_timezone: tzinfo = UTC,
    ) -> int:
        """Count one owner's tasks created in the given calendar day.

        Task records and their permanent identifiers are retained.  The caller
        supplies the display timezone, so the visible counter resets at that
        timezone's midnight without any scheduled deletion or database reset.
        """
        reference = reference_at or utc_now()
        if reference.tzinfo is None:
            raise ValueError("مرجع الوقت للعداد اليومي يجب أن يحتوي منطقة زمنية")
        local = reference.astimezone(day_timezone)
        start_local = datetime(local.year, local.month, local.day, tzinfo=day_timezone)
        end_local = start_local + timedelta(days=1)
        start_utc = encode_time(start_local.astimezone(UTC))
        end_utc = encode_time(end_local.astimezone(UTC))
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                """
                SELECT COUNT(*) AS task_count FROM agent_tasks
                WHERE owner_id = ? AND created_at >= ? AND created_at < ?
                """,
                (owner_id, start_utc, end_utc),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row["task_count"] if row else 0)

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
        """Promote legacy scheduled tasks and materialize due persistent schedules."""
        now_dt = utc_now()
        now = encode_time(now_dt)
        db = await self._get_db()
        promoted = 0
        async with self._lock:
            await db.execute("BEGIN IMMEDIATE")
            try:
                legacy = await db.execute(
                    """
                    UPDATE agent_tasks SET status = 'queued', updated_at = ?, sequence = 0
                    WHERE status = 'scheduled' AND due_at <= ?
                    """,
                    (now, now),
                )
                promoted += int(legacy.rowcount)

                async with db.execute(
                    """
                    SELECT * FROM scheduled_jobs
                    WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
                    ORDER BY next_run_at, id
                    """,
                    (now,),
                ) as cursor:
                    due_rows = await cursor.fetchall()

                for row in due_rows:
                    job = self._scheduled_from_row(row)
                    async with db.execute(
                        """
                        SELECT 1 FROM agent_tasks
                        WHERE schedule_job_id = ? AND status IN ('queued', 'running')
                        LIMIT 1
                        """,
                        (job.id,),
                    ) as cursor:
                        already_active = await cursor.fetchone()
                    if already_active is None:
                        async with db.execute(
                            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM agent_tasks "
                            "WHERE owner_id = ? AND status IN ('queued', 'running')",
                            (job.owner_id,),
                        ) as cursor:
                            sequence = int((await cursor.fetchone())["next_sequence"])
                        await db.execute(
                            """
                            INSERT INTO agent_tasks
                            (owner_id, chat_id, prompt, status, created_at, updated_at, sequence,
                             attachments_json, execution_mode, schedule_job_id)
                            VALUES (?, ?, ?, 'queued', ?, ?, ?, '[]', NULL, ?)
                            """,
                            (
                                job.owner_id,
                                job.chat_id,
                                job.prompt,
                                now,
                                now,
                                sequence,
                                job.id,
                            ),
                        )
                        promoted += 1

                    if job.repeat_seconds:
                        next_due = job.next_run_at or now_dt
                        while next_due <= now_dt:
                            next_due += timedelta(seconds=job.repeat_seconds)
                        await db.execute(
                            """
                            UPDATE scheduled_jobs
                            SET next_run_at = ?, updated_at = ?, last_run_at = ?, last_error = NULL
                            WHERE id = ?
                            """,
                            (encode_time(next_due), now, now, job.id),
                        )
                    else:
                        await db.execute(
                            """
                            UPDATE scheduled_jobs
                            SET enabled = 0, next_run_at = NULL, updated_at = ?, last_run_at = ?, last_error = NULL
                            WHERE id = ?
                            """,
                            (now, now, job.id),
                        )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return promoted


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
            if task.schedule_job_id is not None:
                await db.execute(
                    """
                    UPDATE scheduled_jobs
                    SET last_run_at = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, None if success else error, now, task.schedule_job_id),
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
