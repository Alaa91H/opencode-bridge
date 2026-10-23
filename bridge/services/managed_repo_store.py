"""Persistent managed-repository allow-list for GitHub administration.

The configured ``GITHUB_ALLOWED_REPOS`` list stays the deployment-level floor:
repositories added from Telegram live here, so the owner can take over a new
repository (or one created from the bot) without editing ``.env`` and
restarting the service.  The workspace manager is extended at start-up and on
every addition; a removal only conflicts with the static configuration, never
with a repository that the config already protects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bridge.infrastructure.database.sqlite import BridgeDatabase


class ManagedRepoStore:
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
            CREATE TABLE IF NOT EXISTS managed_repos (
                slug TEXT PRIMARY KEY COLLATE NOCASE,
                owner_id TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL
            )
            """
        )
        await db.commit()

    async def add(self, slug: str, owner_id: str = "") -> None:
        db = await self._get_db()
        now = datetime.now(UTC).isoformat()
        async with self._lock:
            await db.execute(
                """
                INSERT INTO managed_repos(slug, owner_id, added_at)
                VALUES (?, ?, ?)
                ON CONFLICT(slug) DO NOTHING
                """,
                (slug, owner_id, now),
            )
            await db.commit()

    async def remove(self, slug: str) -> bool:
        db = await self._get_db()
        async with self._lock:
            cursor = await db.execute(
                "DELETE FROM managed_repos WHERE slug = ?", (slug,)
            )
            await db.commit()
        return bool(cursor.rowcount)

    async def list(self) -> tuple[str, ...]:
        db = await self._get_db()
        async with db.execute(
            "SELECT slug FROM managed_repos ORDER BY slug COLLATE NOCASE"
        ) as cursor:
            rows = await cursor.fetchall()
        return tuple(str(row["slug"]) for row in rows)

    async def close(self) -> None:
        if self._owns_database:
            await self.database.close()
