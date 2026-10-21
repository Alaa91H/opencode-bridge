"""Persistent active-workspace selection for Telegram users."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bridge.infrastructure.database.sqlite import BridgeDatabase


@dataclass(frozen=True)
class ActiveWorkspace:
    owner_id: str
    repo_slug: str
    directory: str
    updated_at: datetime


class WorkspaceStore:
    def __init__(self, db_path: Path, database: BridgeDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self.database = database or BridgeDatabase(self.db_path)
        self._owns_database = database is None
        self._lock = self.database.transaction_lock

    async def _get_db(self):
        return await self.database.connect()

    async def init(self) -> None:
        db = await self._get_db()
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS active_workspaces (
                owner_id TEXT PRIMARY KEY,
                repo_slug TEXT NOT NULL,
                directory TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()

    async def get(self, owner_id: str) -> ActiveWorkspace | None:
        db = await self._get_db()
        async with self._lock:
            async with db.execute(
                "SELECT owner_id, repo_slug, directory, updated_at FROM active_workspaces WHERE owner_id = ?",
                (owner_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return ActiveWorkspace(
            owner_id=str(row["owner_id"]),
            repo_slug=str(row["repo_slug"]),
            directory=str(row["directory"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    async def set(self, owner_id: str, repo_slug: str, directory: str) -> ActiveWorkspace:
        now = datetime.now(UTC)
        db = await self._get_db()
        async with self._lock:
            await db.execute(
                """
                INSERT INTO active_workspaces(owner_id, repo_slug, directory, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    repo_slug = excluded.repo_slug,
                    directory = excluded.directory,
                    updated_at = excluded.updated_at
                """,
                (owner_id, repo_slug, directory, now.isoformat()),
            )
            await db.commit()
        return ActiveWorkspace(owner_id, repo_slug, directory, now)

    async def close(self) -> None:
        if self._owns_database:
            await self.database.close()
