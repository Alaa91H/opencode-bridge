"""Persistent versioned prompt drafts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bridge.infrastructure.database.sqlite import BridgeDatabase


@dataclass(frozen=True)
class Draft:
    id: int
    owner_id: str
    name: str
    status: str
    version: int
    prompt_text: str
    attachments: tuple[dict[str, Any], ...]


class DraftStore:
    def __init__(self, database: BridgeDatabase):
        self.database = database

    async def _snapshot(self, db, draft_id: int, version: int, prompt: str, attachments) -> None:
        await db.execute(
            "INSERT INTO draft_versions(draft_id,version,prompt_text,attachments_json,created_at) VALUES (?,?,?,?,datetime('now'))",
            (draft_id, version, prompt, json.dumps(attachments, ensure_ascii=False, separators=(",", ":"))),
        )

    async def create(self, owner_id: str, name: str) -> Draft:
        async with self.database.transaction(immediate=True) as db:
            cur = await db.execute(
                "INSERT INTO drafts(owner_id,name,status,current_version,created_at,updated_at) VALUES (?,?,'editing',1,datetime('now'),datetime('now'))",
                (owner_id, name),
            )
            draft_id = int(cur.lastrowid)
            await self._snapshot(db, draft_id, 1, "", [])
        return await self.get(owner_id, name)

    async def get(self, owner_id: str, name: str) -> Draft | None:
        db = await self.database.connect()
        async with db.execute(
            """SELECT d.id,d.owner_id,d.name,d.status,d.current_version,v.prompt_text,v.attachments_json
               FROM drafts d JOIN draft_versions v ON v.draft_id=d.id AND v.version=d.current_version
               WHERE d.owner_id=? AND d.name=?""", (owner_id, name),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Draft(int(row["id"]), row["owner_id"], row["name"], row["status"], int(row["current_version"]),
                     row["prompt_text"], tuple(json.loads(row["attachments_json"])))

    async def append(self, owner_id: str, name: str, text: str = "", attachments=()) -> Draft:
        current = await self.get(owner_id, name)
        if current is None:
            raise KeyError(name)
        prompt = current.prompt_text + (("\n" if current.prompt_text and text else "") + text)
        files = [*current.attachments, *attachments]
        version = current.version + 1
        async with self.database.transaction(immediate=True) as db:
            await self._snapshot(db, current.id, version, prompt, files)
            await db.execute("UPDATE drafts SET current_version=?,updated_at=datetime('now') WHERE id=?", (version,current.id))
        return await self.get(owner_id, name)

    async def clear(self, owner_id: str, name: str) -> Draft:
        current = await self.get(owner_id, name)
        if current is None:
            raise KeyError(name)
        version = current.version + 1
        async with self.database.transaction(immediate=True) as db:
            await self._snapshot(db, current.id, version, "", [])
            await db.execute("UPDATE drafts SET current_version=?,updated_at=datetime('now') WHERE id=?", (version,current.id))
        return await self.get(owner_id, name)

    async def save(self, owner_id: str, name: str) -> Draft:
        async with self.database.transaction(immediate=True) as db:
            await db.execute("UPDATE drafts SET status='saved',updated_at=datetime('now') WHERE owner_id=? AND name=?", (owner_id,name))
        draft = await self.get(owner_id, name)
        if draft is None:
            raise KeyError(name)
        return draft
