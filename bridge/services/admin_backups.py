"""Manage offline SQLite snapshots for the Telegram control panel."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from bridge.services.host_credentials import secure_write


class AdminBackups:
    def __init__(self, project: Path, release_root: Path | None = None):
        self.project = Path(project)
        self.release_root = release_root or Path("/home/ubuntu/opencode-backups/releases")
        self.root = self.project / "runtime" / "admin-backups"
        self.lock = asyncio.Lock()

    def list(self) -> list[dict]:
        results = []
        for path in self.root.glob("backup-*"):
            if path.suffix not in {".db", ".age"} or path.is_symlink() or not path.is_file():
                continue
            meta = (path.with_name(path.name + ".json") if path.suffix == ".age"
                    else path.with_suffix(".json"))
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                results.append({**info, "name": path.name,
                                "bytes": path.stat().st_size})
            except (OSError, ValueError):
                continue
        if self.release_root.is_dir():
            import re
            for file in self.release_root.iterdir():
                if not re.fullmatch(
                    r"opencode-bridge-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z\.tar\.gz",
                    file.name,
                ) or file.is_symlink() or not file.is_file():
                    continue
                results.append({
                    "name": file.name, "bytes": file.stat().st_size,
                    "created_utc": datetime.fromtimestamp(file.stat().st_mtime, UTC).isoformat(),
                    "includes_keys": False, "encrypted": False, "kind": "release"})
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
            # Stream instead of loading the entire database into memory.
            # The host may have only 1 GiB RAM, while SQLite may be larger.
            destination = self.root / name
            fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=self.root)
            digest = hashlib.sha256()
            try:
                with os.fdopen(fd, "wb") as sink, target.open("rb") as stream:
                    os.chmod(temporary, 0o600)
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        sink.write(chunk)
                        digest.update(chunk)
                    sink.flush()
                    os.fsync(sink.fileno())
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
        info = {"created_utc": datetime.now(UTC).isoformat(),
                "includes_keys": False, "sha256": digest.hexdigest()}
        secure_write(destination.with_suffix(".json"), json.dumps(info).encode())
        return {"name": name, "bytes": destination.stat().st_size, **info}

    def get(self, name: str) -> Path:
        import re
        if re.fullmatch(r"opencode-bridge-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z\.tar\.gz", name):
            path = self.release_root / name
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError(name)
            return path
        if not re.fullmatch(r"backup-[0-9]{8}T?[0-9]{6}-[a-f0-9]{8}\.(db|age)", name):
            raise ValueError("Invalid backup name")
        path = self.root / name
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(name)
        return path

    def delete(self, name: str) -> None:
        path = self.get(name)
        path.unlink()
        if path.parent == self.release_root:
            path.with_name(path.name + ".sha256").unlink(missing_ok=True)
        else:
            metadata = (path.with_name(path.name + ".json") if path.suffix == ".age"
                        else path.with_suffix(".json"))
            metadata.unlink(missing_ok=True)

    async def create_encrypted(self) -> dict:
        """Secret-inclusive backup is never sent or stored unencrypted."""
        async with self.lock:
            return await asyncio.to_thread(self._encrypted_snapshot)

    def _encrypted_snapshot(self) -> dict:
        recipient = self.project / "runtime" / "backup-age-recipient"
        if recipient.is_symlink() or not recipient.is_file():
            raise ValueError("Configure runtime/backup-age-recipient with your age public key")
        key = recipient.read_text(encoding="ascii").strip()
        if not key.startswith("age1") or len(key) > 160 or not shutil.which("age"):
            raise ValueError("A valid age recipient and the age program are required")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = datetime.now(UTC).strftime("backup-%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:8]
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            folder = Path(temporary)
            plaintext = folder / "snapshot.tar.gz"
            database = self.project / "sessions.db"
            if database.is_file():
                src = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                dst = sqlite3.connect(folder / "sessions.db")
                try:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Backup database verification failed")
                finally:
                    dst.close()
                    src.close()
            with tarfile.open(plaintext, "w:gz") as archive:
                if (folder / "sessions.db").is_file():
                    archive.add(folder / "sessions.db", arcname="sessions.db")
                for rel in (".env", "runtime/private/provider-keys.json",
                            "runtime/private/host-credentials.json",
                            "runtime/memory-policy.json", "runtime/auto-update-enabled"):
                    file = self.project / rel
                    if file.is_file() and not file.is_symlink():
                        archive.add(file, arcname=rel)
            encrypted = folder / (name + ".age")
            subprocess.run(["age", "-r", key, "-o", str(encrypted), str(plaintext)],  # noqa: S603,S607 -- fixed executable and validated recipient
                           check=True, capture_output=True, timeout=120)
            output = self.root / encrypted.name
            secure_write(output, encrypted.read_bytes())
        metadata = {
            "created_utc": datetime.now(UTC).isoformat(), "includes_keys": True,
            "encrypted": True, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
        secure_write(output.with_name(output.name + ".json"), json.dumps(metadata).encode())
        return {"name": output.name, "bytes": output.stat().st_size, **metadata}

    def _backup_usable(self, item: dict) -> bool:
        """Only verified backups qualify as retained recovery candidates."""
        try:
            path = self.get(item["name"])
            if item.get("encrypted"):
                if path.suffix != ".age":
                    return False
                expected = item.get("sha256")
                if not isinstance(expected, str) or len(expected) != 64:
                    return False
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                return path.stat().st_size > 0 and digest.hexdigest() == expected
            if path.suffix != ".db":
                return False
            # Valid SQLite alone does not prove that the snapshot is unchanged.
            # Older unsigned snapshots remain visible for manual recovery,
            # but must not authorize automatic deletion of newer copies.
            expected = item.get("sha256")
            if not isinstance(expected, str) or len(expected) != 64:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                return False
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
                return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return False

    def purge_after_verified_update(self) -> int:
        """Prune only after preserving two verified backups per category.

        Invalid snapshots are never counted toward the minimum number of
        restore points. They are also retained for manual diagnosis.
        """
        candidates = [item for item in self.list() if item.get("kind") != "release"]
        retained: dict[str, int] = {"database": 0, "encrypted": 0}
        removed = 0
        for item in candidates:
            group = "encrypted" if item.get("encrypted") else "database"
            if not self._backup_usable(item):
                continue
            if retained[group] < 2:
                retained[group] += 1
                continue
            self.delete(item["name"])
            removed += 1
        return removed
