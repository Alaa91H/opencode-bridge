"""Manage offline SQLite snapshots for the Telegram control panel."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from bridge.services.host_credentials import secure_write


class AdminBackups:
    def __init__(self, project: Path):
        self.project = Path(project)
        self.root = self.project / "runtime" / "admin-backups"
        self.lock = asyncio.Lock()

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        results = []
        for path in self.root.glob("backup-*.db"):
            if path.is_symlink() or not path.is_file():
                continue
            meta = path.with_suffix(".json")
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                results.append({**info, "name": path.name,
                                "bytes": path.stat().st_size})
            except (OSError, ValueError):
                continue
        return sorted(results, key=lambda x: x["name"], reverse=True)[:30]

    async def create(self) -> dict:
        async with self.lock:
            return await asyncio.to_thread(self._create_sync)

    def _create_sync(self) -> dict:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = datetime.now(UTC).strftime("backup-%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:8] + ".db"
        source = sqlite3.connect("file:" + str(self.project / "sessions.db") + "?mode=ro", uri=True)
        with tempfile.TemporaryDirectory(dir=self.root) as folder:
            target = Path(folder) / "snapshot.db"
            output = sqlite3.connect(target)
            try:
                source.backup(output)
                if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Snapshot failed integrity check")
            finally:
                output.close()
                source.close()
            destination = self.root / name
            secure_write(destination, target.read_bytes())
        info = {"created_utc": datetime.now(UTC).isoformat(),
                "includes_keys": False}
        secure_write(destination.with_suffix(".json"), json.dumps(info).encode())
        return {"name": name, "bytes": destination.stat().st_size, **info}

    def get(self, name: str) -> Path:
        import re
        if not re.fullmatch(r"backup-[0-9]{8}T?[0-9]{6}-[a-f0-9]{8}\.db", name):
            raise ValueError("Invalid backup name")
        path = self.root / name
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(name)
        return path

    def delete(self, name: str) -> None:
        path = self.get(name)
        path.unlink()
        path.with_suffix(".json").unlink(missing_ok=True)
