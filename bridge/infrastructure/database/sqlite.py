"""Shared SQLite lifecycle and transaction primitives for Bridge stores."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


class BridgeDatabase:
    """Own one configured SQLite connection and serialize its transactions."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._connection: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()
        self.transaction_lock = asyncio.Lock()
        self._last_integrity_check = 0.0

    async def connect(self) -> aiosqlite.Connection:
        if self._connection is None:
            async with self._connect_lock:
                if self._connection is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    db = await aiosqlite.connect(str(self.path))
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA synchronous=NORMAL")
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
                    self._connection = db
        return self._connection

    @asynccontextmanager
    async def transaction(self, *, immediate: bool = False) -> AsyncIterator[aiosqlite.Connection]:
        db = await self.connect()
        async with self.transaction_lock:
            await db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield db
            except BaseException:
                await db.rollback()
                raise
            else:
                await db.commit()

    async def checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
        normalized = mode.upper()
        if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError(f"Unsupported WAL checkpoint mode: {mode}")
        db = await self.connect()
        async with self.transaction_lock:
            async with db.execute(f"PRAGMA wal_checkpoint({normalized})") as cursor:
                row = await cursor.fetchone()
        return tuple(int(value) for value in row)

    async def periodic_integrity_check(self, interval_seconds: float = 86400.0) -> str | None:
        """Run integrity_check at most once per interval; None means it was not due."""
        now = time.monotonic()
        if self._last_integrity_check and now - self._last_integrity_check < max(0.0, interval_seconds):
            return None
        result = await self.integrity_check()
        self._last_integrity_check = now
        return result

    async def integrity_check(self) -> str:
        db = await self.connect()
        async with self.transaction_lock, db.execute("PRAGMA integrity_check") as cursor:
            row = await cursor.fetchone()
        return str(row[0]) if row else "unknown"

    async def close(self) -> None:
        async with self._connect_lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None
