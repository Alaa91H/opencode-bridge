"""Persistent active-workspace selection for Telegram users."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class ActiveWorkspace:
    owner_id: str
    repo_slug: str
    directory: str
    updated_at: datetime


class WorkspaceStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(str(self.db_path))
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute("PRAGMA busy_timeout=5000")
        return self._db

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
        now = datetime.now(timezone.utc)
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
        if self._db is not None:
            await self._db.close()
            self._db = None
