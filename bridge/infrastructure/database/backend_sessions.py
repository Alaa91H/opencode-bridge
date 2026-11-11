"""Persistence for per-backend conversation ids and owner preferences.

Kept in its own tables so the legacy ``sessions`` schema and every OpenCode code
path remain untouched. A session key is ``(owner, backend, workspace)`` because a
Freebuff conversation belongs to one working directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from bridge.infrastructure.database.sqlite import BridgeDatabase

ALLOWED_BACKENDS = frozenset({"opencode", "freebuff", "auto"})


@dataclass(frozen=True)
class BackendSession:
    owner_id: str
    backend: str
    workspace_key: str
    session_id: str
    model: str | None
    updated_at: datetime


class BackendSessionStore:
    def __init__(self, database: BridgeDatabase) -> None:
        self.database = database

    async def init(self) -> None:
        db = await self.database.connect()
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS backend_sessions (
                owner_id TEXT NOT NULL,
                backend TEXT NOT NULL,
                workspace_key TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                model TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (owner_id, backend, workspace_key)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS backend_preferences (
                owner_id TEXT PRIMARY KEY,
                backend TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()

    async def get_session(
        self, owner_id: str, backend: str, workspace_key: str
    ) -> BackendSession | None:
        await self.init()
        db = await self.database.connect()
        async with db.execute(
            """
            SELECT owner_id, backend, workspace_key, session_id, model, updated_at
            FROM backend_sessions
            WHERE owner_id = ? AND backend = ? AND workspace_key = ?
            """,
            (owner_id, backend, workspace_key),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return BackendSession(
            owner_id=str(row["owner_id"]),
            backend=str(row["backend"]),
            workspace_key=str(row["workspace_key"]),
            session_id=str(row["session_id"] or ""),
            model=str(row["model"]) if row["model"] else None,
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    async def upsert_session(
        self,
        owner_id: str,
        backend: str,
        workspace_key: str,
        session_id: str,
        model: str | None = None,
    ) -> None:
        await self.init()
        db = await self.database.connect()
        now = datetime.now(UTC).isoformat()
        async with self.database.transaction(immediate=True):
            await db.execute(
                """
                INSERT INTO backend_sessions
                    (owner_id, backend, workspace_key, session_id, model, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, backend, workspace_key) DO UPDATE SET
                    session_id = excluded.session_id,
                    model = COALESCE(excluded.model, backend_sessions.model),
                    updated_at = excluded.updated_at
                """,
                (owner_id, backend, workspace_key, session_id, model, now),
            )

    async def delete_session(self, owner_id: str, backend: str, workspace_key: str) -> bool:
        await self.init()
        db = await self.database.connect()
        async with self.database.transaction(immediate=True):
            cursor = await db.execute(
                """
                DELETE FROM backend_sessions
                WHERE owner_id = ? AND backend = ? AND workspace_key = ?
                """,
                (owner_id, backend, workspace_key),
            )
        return cursor.rowcount > 0

    async def get_preference(self, owner_id: str) -> str | None:
        await self.init()
        db = await self.database.connect()
        async with db.execute(
            "SELECT backend FROM backend_preferences WHERE owner_id = ?", (owner_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        value = str(row["backend"] or "").strip().lower()
        return value if value in ALLOWED_BACKENDS else None

    async def set_preference(self, owner_id: str, backend: str) -> None:
        normalized = backend.strip().lower()
        if normalized not in ALLOWED_BACKENDS:
            raise ValueError("unsupported agent backend")
        await self.init()
        db = await self.database.connect()
        now = datetime.now(UTC).isoformat()
        async with self.database.transaction(immediate=True):
            await db.execute(
                """
                INSERT INTO backend_preferences(owner_id, backend, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    backend = excluded.backend,
                    updated_at = excluded.updated_at
                """,
                (owner_id, normalized, now),
            )
