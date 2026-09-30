"""Explicit, ordered SQLite migrations for OpenCode Bridge 2.0."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from bridge.infrastructure.database.sqlite import BridgeDatabase


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n".join(self.statements).encode()
        return hashlib.sha256(payload).hexdigest()


MIGRATIONS = (
    Migration(1, "legacy_task_queue_schema", (
        """CREATE TABLE IF NOT EXISTS agent_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, due_at TEXT,
            repeat_seconds INTEGER, sequence INTEGER NOT NULL DEFAULT 0,
            started_at TEXT, completed_at TEXT, last_error TEXT)""",
        """CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
            prompt TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_run_at TEXT,
            repeat_seconds INTEGER, timezone_name TEXT NOT NULL DEFAULT 'UTC',
            last_run_at TEXT, last_error TEXT, UNIQUE(owner_id, name))""",
        """CREATE TABLE IF NOT EXISTS pending_attachment_batches (
            owner_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
            attachments_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            PRIMARY KEY(owner_id, chat_id))""",

        "ALTER TABLE agent_tasks ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE agent_tasks ADD COLUMN activity_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE agent_tasks ADD COLUMN execution_mode TEXT",
        "ALTER TABLE agent_tasks ADD COLUMN status_message_id INTEGER",
        "ALTER TABLE agent_tasks ADD COLUMN schedule_job_id INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_status_sequence ON agent_tasks(owner_id, status, sequence, id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_due ON agent_tasks(status, due_at)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_created ON agent_tasks(owner_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_owner_name ON scheduled_jobs(owner_id, name COLLATE NOCASE)",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due ON scheduled_jobs(enabled, next_run_at)",
        "CREATE INDEX IF NOT EXISTS idx_pending_attachment_expiry ON pending_attachment_batches(expires_at)",
    )),
    Migration(2, "v2_core_schema", (
        """CREATE TABLE IF NOT EXISTS task_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
            attempt INTEGER NOT NULL, started_at TEXT, finished_at TEXT,
            status TEXT NOT NULL, error TEXT, UNIQUE(task_id, attempt))""",
        """CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
            event_type TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER,
            owner_id TEXT NOT NULL, storage_key TEXT, sha256 TEXT,
            claimed_mime TEXT, detected_mime TEXT, size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS task_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
            kind TEXT NOT NULL, storage_key TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE, definition_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(owner_id, name))""",
        """CREATE TABLE IF NOT EXISTS schedule_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL,
            task_id INTEGER, scheduled_for TEXT NOT NULL, started_at TEXT,
            finished_at TEXT, status TEXT NOT NULL, error TEXT,
            FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
            provider_session_id TEXT NOT NULL, model TEXT, title TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS user_settings (
            owner_id TEXT PRIMARY KEY, settings_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS resource_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL,
            cpu_percent REAL, memory_bytes INTEGER, disk_free_bytes INTEGER,
            payload_json TEXT NOT NULL DEFAULT '{}')""",
        """CREATE TABLE IF NOT EXISTS failure_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER,
            component TEXT NOT NULL, category TEXT NOT NULL, message TEXT,
            retryable INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_task_attempts_task ON task_attempts(task_id, attempt)",
        "CREATE INDEX IF NOT EXISTS idx_task_events_task_created ON task_events(task_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_attachments_task ON attachments(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_attachments_sha ON attachments(sha256)",
        "CREATE INDEX IF NOT EXISTS idx_task_outputs_task ON task_outputs(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule_time ON schedule_runs(schedule_id, scheduled_for)",
        "CREATE INDEX IF NOT EXISTS idx_agent_sessions_owner_updated ON agent_sessions(owner_id, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_resource_snapshots_time ON resource_snapshots(captured_at)",
        "CREATE INDEX IF NOT EXISTS idx_failure_records_task_time ON failure_records(task_id, created_at)",
    )),
    Migration(3, "durable_queue_v2", (
        "ALTER TABLE agent_tasks ADD COLUMN public_id TEXT",
        "ALTER TABLE agent_tasks ADD COLUMN idempotency_key TEXT",
        "ALTER TABLE agent_tasks ADD COLUMN lease_owner TEXT",
        "ALTER TABLE agent_tasks ADD COLUMN lease_expires_at TEXT",
        "ALTER TABLE agent_tasks ADD COLUMN heartbeat_at TEXT",
        "ALTER TABLE agent_tasks ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE agent_tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE agent_tasks ADD COLUMN checkpoint_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE agent_tasks ADD COLUMN next_attempt_at TEXT",
        "ALTER TABLE agent_tasks ADD COLUMN retry_after_seconds REAL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tasks_public_id ON agent_tasks(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tasks_idempotency ON agent_tasks(idempotency_key) WHERE idempotency_key IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_claimable ON agent_tasks(status, next_attempt_at, priority DESC, created_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_lease_expiry ON agent_tasks(status, lease_expires_at)",
    )),
    Migration(4, "input_idempotency", (
        """CREATE TABLE IF NOT EXISTS idempotency_records (
            scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            owner_id TEXT, task_id INTEGER, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(scope, idempotency_key))""",
        """CREATE TABLE IF NOT EXISTS schedule_occurrences (
            schedule_key TEXT NOT NULL, occurrence_key TEXT NOT NULL,
            task_id INTEGER, created_at TEXT NOT NULL,
            PRIMARY KEY(schedule_key, occurrence_key))""",
        "CREATE INDEX IF NOT EXISTS idx_idempotency_task ON idempotency_records(task_id)",
    )),
    Migration(5, "scheduler_v2", (
        "ALTER TABLE schedules ADD COLUMN next_run_at TEXT",
        "ALTER TABLE schedules ADD COLUMN timezone_name TEXT NOT NULL DEFAULT 'UTC'",
        "ALTER TABLE schedules ADD COLUMN misfire_policy TEXT NOT NULL DEFAULT 'run_once'",
        "ALTER TABLE schedules ADD COLUMN overlap_policy TEXT NOT NULL DEFAULT 'forbid'",
        "ALTER TABLE schedules ADD COLUMN last_run_at TEXT",
        "ALTER TABLE schedules ADD COLUMN running_task_id INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(enabled, next_run_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_runs_occurrence ON schedule_runs(schedule_id, scheduled_for)",
    )),
    Migration(6, "draft_editor", (
        """CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'editing',
            current_version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(owner_id,name)
        )""",
        """CREATE TABLE IF NOT EXISTS draft_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            prompt_text TEXT NOT NULL DEFAULT '',
            attachments_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            UNIQUE(draft_id,version)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_drafts_owner_updated ON drafts(owner_id,updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_draft_versions_draft ON draft_versions(draft_id,version DESC)",
    )),
    Migration(7, "attachment_storage_v2", (
        """CREATE TABLE IF NOT EXISTS storage_blobs (
            sha256 TEXT PRIMARY KEY,
            storage_key TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            reference_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_referenced_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS attachment_refs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blob_sha256 TEXT NOT NULL REFERENCES storage_blobs(sha256) ON DELETE RESTRICT,
            owner_id TEXT NOT NULL,
            task_id INTEGER,
            file_id TEXT,
            file_unique_id TEXT,
            claimed_mime TEXT,
            detected_mime TEXT,
            size_bytes INTEGER NOT NULL,
            retention_until TEXT,
            scan_state TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_attachment_refs_owner ON attachment_refs(owner_id,created_at)",
        "CREATE INDEX IF NOT EXISTS idx_attachment_refs_blob ON attachment_refs(blob_sha256)",
        "CREATE INDEX IF NOT EXISTS idx_attachment_refs_retention ON attachment_refs(retention_until)",
    )),
)


class MigrationRunner:
    def __init__(self, database: BridgeDatabase) -> None:
        self.database = database

    async def migrate(self) -> list[int]:
        db = await self.database.connect()
        async with self.database.transaction(immediate=True):
            await db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                checksum TEXT NOT NULL, applied_at TEXT NOT NULL)""")
        applied: list[int] = []
        for migration in MIGRATIONS:
            async with db.execute(
                "SELECT checksum FROM schema_migrations WHERE version = ?", (migration.version,)
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                if str(row["checksum"]) != migration.checksum:
                    raise RuntimeError(f"Migration checksum mismatch for version {migration.version}")
                continue
            async with self.database.transaction(immediate=True):
                for statement in migration.statements:
                    if statement.startswith("ALTER TABLE agent_tasks"):
                        async with db.execute("PRAGMA table_info(agent_tasks)") as cursor:
                            columns = {str(row[1]) for row in await cursor.fetchall()}
                        column = statement.split("ADD COLUMN ", 1)[1].split()[0]
                        if column in columns:
                            continue
                    await db.execute(statement)
                await db.execute(
                    "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                    (migration.version, migration.name, migration.checksum, datetime.now(UTC).isoformat()),
                )
            applied.append(migration.version)
        return applied
