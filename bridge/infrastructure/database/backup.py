"""Verified SQLite online backup and restore primitives."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

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
            datetime.now(UTC).isoformat(),
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
        """Restore only an integrity-checked manifest-bound snapshot, atomically.

        The destination database must be offline. If replacing an existing
        database, SQLite WAL/SHM sidecars must already be checkpointed and
        closed; otherwise activating a new database alongside stale WAL could
        corrupt or silently alter the restored state.
        """
        backup = Path(backup)
        destination = Path(destination)
        if backup.is_symlink() or not backup.is_file():
            raise ValueError("Backup must be an existing regular file")
        if destination.is_symlink() or backup.resolve() == destination.resolve():
            raise ValueError("Cannot restore through a symlink or overwrite the source")
        if any(destination.with_name(destination.name + suffix).exists()
               for suffix in ("-wal", "-shm")):
            raise ValueError("Destination SQLite WAL must be checkpointed first")

        manifest_path = backup.with_suffix(backup.suffix + ".manifest.json")
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("Backup manifest is required before restoration")
        try:
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = BackupManifest(**metadata)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("Invalid backup manifest") from exc
        if not BackupService.verify(backup, manifest):
            raise ValueError("Backup failed checksum or database integrity verification")

        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".restore-", suffix=".db", dir=destination.parent,
        )
        stage = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as target, backup.open("rb") as source:
                if hasattr(os, "fchmod"):
                    os.fchmod(target.fileno(), 0o600)
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if not BackupService.verify(stage, manifest):
                raise ValueError("Staged database differs from verified backup")
            # Rename within the same filesystem preserves the prior database
            # intact if any step above fails.
            os.replace(stage, destination)
            if hasattr(os, "O_DIRECTORY"):
                directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            stage.unlink(missing_ok=True)
