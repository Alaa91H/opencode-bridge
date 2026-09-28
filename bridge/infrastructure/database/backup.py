"""Verified SQLite online backup and restore primitives."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from bridge.infrastructure.database.sqlite import BridgeDatabase


@dataclass(frozen=True)
class BackupManifest:
    created_at: str
    database_sha256: str
    database_bytes: int
    rpo_seconds: int
    rto_seconds: int


class BackupService:
    def __init__(self, database: BridgeDatabase, *, rpo_seconds: int = 3600,
                 rto_seconds: int = 900,
                 encrypt: Callable[[bytes], bytes] | None = None) -> None:
        self.database = database
        self.rpo_seconds = rpo_seconds
        self.rto_seconds = rto_seconds
        self.encrypt = encrypt

    async def create(self, destination: Path) -> BackupManifest:
        source = await self.database.connect()
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = await __import__("aiosqlite").connect(str(destination))
        try:
            await source.backup(target)
            await target.commit()
        finally:
            await target.close()
        payload = destination.read_bytes()
        manifest = BackupManifest(
            datetime.now(timezone.utc).isoformat(),
            hashlib.sha256(payload).hexdigest(), len(payload),
            self.rpo_seconds, self.rto_seconds,
        )
        destination.with_suffix(destination.suffix + ".manifest.json").write_text(
            json.dumps(asdict(manifest), sort_keys=True), encoding="utf-8")
        if self.encrypt is not None:
            destination.with_suffix(destination.suffix + ".enc").write_bytes(self.encrypt(payload))
        return manifest

    @staticmethod
    def verify(path: Path, manifest: BackupManifest) -> bool:
        payload = path.read_bytes()
        if len(payload) != manifest.database_bytes:
            return False
        if hashlib.sha256(payload).hexdigest() != manifest.database_sha256:
            return False
        db = sqlite3.connect(str(path))
        try:
            row = db.execute("PRAGMA integrity_check").fetchone()
            return bool(row and row[0] == "ok")
        finally:
            db.close()

    @staticmethod
    def restore(backup: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(str(backup))
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
