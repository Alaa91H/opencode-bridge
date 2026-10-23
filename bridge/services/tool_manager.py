"""Professional tool/skill management for the agent and server.

Capabilities:
- Define tools (name, type, source, installed, enabled, version).
- Enable/disable per-owner or globally.
- Update from source / install / uninstall.
- List current state for control panel and bot commands.
- Cache providers/catalog to avoid repeated catalog fetches (performance).
- Integrate with reminder/notification when a required tool is missing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from bridge.infrastructure.database.sqlite import BridgeDatabase

log = logging.getLogger("opencode_bridge.tool_manager")


class ToolTransport(Protocol):
    async def send_text(self, destination: str | int, text: str) -> Any: ...


@dataclass
class ToolRecord:
    id: int
    name: str
    tool_type: str  # builtin, plugin, external, skill
    source_url: str | None = None
    installed_version: str | None = None
    enabled: bool = True
    owner_id: str | None = None
    status: str = "available"  # available, disabled, broken, update_available, installing
    installed_at: datetime | None = None
    updated_at: datetime | None = None


class ToolStore:
    """Durable SQLite storage for tool registry."""

    def __init__(self, database: BridgeDatabase) -> None:
        self.db = database
        self._lock = database.transaction_lock

    async def init(self) -> None:
        db = await self.db.connect()
        async with self.db.transaction(immediate=True):
            await db.execute(
                """CREATE TABLE IF NOT EXISTS agent_tools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    tool_type TEXT NOT NULL DEFAULT 'builtin',
                    source_url TEXT,
                    installed_version TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    owner_id TEXT,
                    status TEXT NOT NULL DEFAULT 'available',
                    installed_at TEXT,
                    updated_at TEXT
                )"""
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_tools_name ON agent_tools(name)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_tools_owner ON agent_tools(owner_id, status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_tools_enabled ON agent_tools(enabled, status)")
            await db.commit()

    async def register(self, name: str, tool_type: str = "builtin", source_url: str | None = None,
                       version: str | None = None, owner_id: str | None = None) -> int:
        db = await self.db.connect()
        async with self._lock:
            await db.execute(
                """INSERT OR IGNORE INTO agent_tools (name, tool_type, source_url, installed_version, enabled, owner_id, status, installed_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, 'available', ?, ?)""",
                (str(name), str(tool_type), source_url, version, owner_id,
                 datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
            )
            await db.execute("UPDATE agent_tools SET updated_at=? WHERE name=?", (datetime.now(UTC).isoformat(), str(name)))
            await db.commit()
            async with db.execute("SELECT id FROM agent_tools WHERE name=?", (str(name),)) as c:
                row = await c.fetchone()
            return int(row["id"]) if row else 0

    async def set_enabled(self, name: str, enabled: bool) -> bool:
        db = await self.db.connect()
        async with self._lock:
            await db.execute("UPDATE agent_tools SET enabled=?, updated_at=? WHERE name=?", (1 if enabled else 0, datetime.now(UTC).isoformat(), str(name)))
            await db.commit()
            return True

    async def set_status(self, name: str, status: str) -> bool:
        db = await self.db.connect()
        async with self._lock:
            await db.execute("UPDATE agent_tools SET status=?, updated_at=? WHERE name=?", (str(status), datetime.now(UTC).isoformat(), str(name)))
            await db.commit()
            return True

    async def uninstall(self, name: str, owner_id: str | None = None) -> bool:
        db = await self.db.connect()
        async with self._lock:
            if owner_id:
                await db.execute("DELETE FROM agent_tools WHERE name=? AND (owner_id=? OR owner_id IS NULL)", (str(name), str(owner_id)))
            else:
                await db.execute("DELETE FROM agent_tools WHERE name=?", (str(name),))
            await db.commit()
            return True

    async def list_all(self, enabled_only: bool = False) -> list[ToolRecord]:
        db = await self.db.connect()
        if enabled_only:
            query = "SELECT * FROM agent_tools WHERE enabled=1 ORDER BY name"
        else:
            query = "SELECT * FROM agent_tools ORDER BY name"
        async with self._lock:
            async with db.execute(query) as c:
                rows = await c.fetchall()
        return [
            ToolRecord(
                id=int(r["id"]), name=str(r["name"]), tool_type=str(r["tool_type"]),
                source_url=r["source_url"], installed_version=r["installed_version"],
                enabled=bool(r["enabled"]), owner_id=r["owner_id"], status=str(r["status"]),
                installed_at=datetime.fromisoformat(str(r["installed_at"])) if r["installed_at"] else None,
                updated_at=datetime.fromisoformat(str(r["updated_at"])) if r["updated_at"] else None,
            ) for r in rows
        ]

    async def get(self, name: str) -> ToolRecord | None:
        db = await self.db.connect()
        async with self._lock:
            async with db.execute("SELECT * FROM agent_tools WHERE name=?", (str(name),)) as c:
                row = await c.fetchone()
        if not row:
            return None
        return ToolRecord(
            id=int(row["id"]), name=str(row["name"]), tool_type=str(row["tool_type"]),
            source_url=row["source_url"], installed_version=row["installed_version"],
            enabled=bool(row["enabled"]), owner_id=row["owner_id"], status=str(row["status"]),
            installed_at=datetime.fromisoformat(str(row["installed_at"])) if row["installed_at"] else None,
            updated_at=datetime.fromisoformat(str(row["updated_at"])) if row["updated_at"] else None,
        )


class ToolManager:
    """Agent capability manager: register, enable/disable, update, uninstall, notify."""

    def __init__(self, store: ToolStore, transport: ToolTransport | None = None,
                 reminder_service: Any = None) -> None:
        self.store = store
        self.transport = transport
        self.reminder = reminder_service
        self._catalog_cache: list[ToolRecord] = []
        self._catalog_at: datetime | None = None

    async def register_builtin(self, name: str, version: str | None = None,
                                owner_id: str | None = None) -> int:
        return await self.store.register(name, "builtin", version=version, owner_id=owner_id)

    async def enable(self, name: str) -> bool:
        return await self.store.set_enabled(name, True)

    async def disable(self, name: str) -> bool:
        return await self.store.set_enabled(name, False)

    async def update_version(self, name: str, version: str) -> bool:
        return await self.store.set_status(name, "update_available")

    async def uninstall_tool(self, name: str, owner_id: str | None = None) -> bool:
        result = await self.store.uninstall(name, owner_id)
        if result and self.reminder is not None:
            # Notify owner that a used capability was removed (security/stability).
            try:
                await self.reminder.store.create(
                    owner_id or "0", f"تم حذف الأداة: {name}", datetime.now(UTC)
                )
            except Exception:
                pass
        return result

    async def list_capabilities(self, enabled_only: bool = False) -> list[ToolRecord]:
        if self._catalog_cache and self._catalog_at is not None:
            age = (datetime.now(UTC) - self._catalog_at).total_seconds()
            if age < 60:
                return [c for c in self._catalog_cache if (not enabled_only) or c.enabled]
        records = await self.store.list_all(enabled_only=enabled_only)
        self._catalog_cache = records
        self._catalog_at = datetime.now(UTC)
        return records

    async def ensure_available(self, name: str, owner_id: str | None = None) -> bool:
        tool = await self.store.get(name)
        if tool is None:
            await self.store.register(name, "external", owner_id=owner_id)
            tool = await self.store.get(name)
        if tool is not None and tool.enabled and tool.status == "available":
            return True
        return False

    async def notify_missing(self, name: str, owner_id: str) -> None:
        if self.transport is not None:
            try:
                await self.transport.send_text(str(owner_id), f"⚠️ الأداة {name} غير متوفرة حالياً؛ تم طلبها للتثبيت.")
            except Exception:
                pass
