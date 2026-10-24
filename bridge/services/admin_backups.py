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
import zipfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridge.services.host_credentials import secure_write


class AdminBackups:
    RETENTION_DAYS = 3

    def __init__(
        self,
        project: Path,
        release_root: Path | None = None,
        *,
        tool_enabled: Callable[[str], bool] | None = None,
    ):
        self.project = Path(project)
        self._tool_enabled = tool_enabled or (lambda _key: True)
        # Derive the default from the project so tests and alternate installs
        # never read or prune the live host's release archives.  For the real
        # checkout this is exactly /home/ubuntu/opencode-backups/releases.
        self.release_root = (
            Path(release_root)
            if release_root is not None
            else self.project.parent / "opencode-backups" / "releases"
        )
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
            created = await asyncio.to_thread(self._create_sync)
            await asyncio.to_thread(self.prune_older_than)
            return created

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
            created = await asyncio.to_thread(self._encrypted_snapshot)
            await asyncio.to_thread(self.prune_older_than)
            return created

    def _encrypted_snapshot(self) -> dict:
        recipient = self.project / "runtime" / "backup-age-recipient"
        if recipient.is_symlink() or not recipient.is_file():
            raise ValueError("Configure runtime/backup-age-recipient with your age public key")
        key = recipient.read_text(encoding="ascii").strip()
        if not self._tool_enabled("age"):
            raise ValueError("The age tool is disabled in tool management")
        if not key.startswith("age1") or len(key) > 160 or not shutil.which("age"):
            raise ValueError("A valid age recipient and the age program are required")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = datetime.now(UTC).strftime("backup-%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:8]
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            folder = Path(temporary)
            plaintext = folder / "snapshot.tar.gz"
            database = self.project / "sessions.db"
            if database.is_symlink() or not database.is_file():
                raise ValueError("Cannot create a recovery backup without a safe SQLite database")
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
                            "runtime/memory-policy.json", "runtime/auto-update-enabled",
                            "runtime/autopilot-mode", "runtime/host-auto-update-enabled"):
                    file = self.project / rel
                    if file.is_file() and not file.is_symlink():
                        archive.add(file, arcname=rel)
            encrypted = folder / (name + ".age")
            subprocess.run(["age", "-r", key, "-o", str(encrypted), str(plaintext)],  # noqa: S603,S607 -- fixed executable and validated recipient
                           check=True, capture_output=True, timeout=120)
            output = self.root / encrypted.name
            if output.is_symlink():
                raise ValueError("Refusing symlink backup destination")
            fd, temporary_file = tempfile.mkstemp(prefix=".encrypted-", dir=self.root)
            digest = hashlib.sha256()
            size = 0
            try:
                with os.fdopen(fd, "wb") as sink, encrypted.open("rb") as stream:
                    os.fchmod(sink.fileno(), 0o600)
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        sink.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    sink.flush()
                    os.fsync(sink.fileno())
                if size == 0:
                    raise ValueError("Encrypted backup was empty")
                os.replace(temporary_file, output)
            finally:
                Path(temporary_file).unlink(missing_ok=True)
        metadata = {
            "created_utc": datetime.now(UTC).isoformat(), "includes_keys": True,
            "encrypted": True, "sha256": digest.hexdigest()}
        try:
            secure_write(output.with_name(output.name + ".json"), json.dumps(metadata).encode())
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        return {"name": output.name, "bytes": size, **metadata}

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

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(path.name + ".json") if path.suffix == ".age" else path.with_suffix(".json")

    @staticmethod
    def _parse_created(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _backup_created_at(self, path: Path) -> datetime:
        metadata = self._metadata_path(path)
        try:
            info = json.loads(metadata.read_text(encoding="utf-8"))
            created = self._parse_created(info.get("created_utc"))
            if created is not None:
                return created
        except (OSError, ValueError, TypeError):
            pass
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)

    def prune_older_than(self, days: int = RETENTION_DAYS) -> int:
        """Permanently delete every backup file older than ``days``.

        The cutoff is wall-clock UTC.  Metadata timestamps are authoritative
        when readable; the file mtime is the fallback so an orphaned or
        corrupted metadata file cannot make an old snapshot immortal.  No
        snapshot is exempt: the zip already sent to Telegram is the off-host
        copy once the local retention window closes.
        """
        if type(days) is not int or days < 1:
            raise ValueError("Retention must be a positive number of days")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        removed = 0
        try:
            backup_paths = list(self.root.glob("backup-*"))
        except OSError:
            backup_paths = []
        candidates: list[tuple[datetime, Path]] = []
        for path in backup_paths:
            if path.suffix not in {".db", ".age"} or path.is_symlink() or not path.is_file():
                continue
            try:
                candidates.append((self._backup_created_at(path), path))
            except OSError:
                continue
        for created, path in candidates:
            if created >= cutoff:
                continue
            try:
                self.delete(path.name)
                removed += 1
            except (OSError, ValueError):
                continue
        if self.release_root.is_dir():
            import re
            pattern = re.compile(r"opencode-bridge-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z\.tar\.gz")
            for path in self.release_root.iterdir():
                if not pattern.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                    continue
                try:
                    created = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                except OSError:
                    continue
                if created < cutoff:
                    try:
                        self.delete(path.name)
                        removed += 1
                    except (OSError, ValueError):
                        continue
        return removed

    def zip_for_delivery(self, name: str) -> tuple[Path, str]:
        """Build a temporary zip containing the snapshot and its metadata."""
        source = self.get(name)
        metadata = self._metadata_path(source)
        fd, temporary = tempfile.mkstemp(prefix=".delivery-", suffix=".zip", dir=self.root)
        os.close(fd)
        archive = Path(temporary)
        try:
            os.chmod(archive, 0o600)
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as bundle:
                bundle.write(source, arcname=source.name)
                if metadata.is_file() and not metadata.is_symlink():
                    bundle.write(metadata, arcname=metadata.name)
            if archive.stat().st_size == 0:
                raise ValueError("Backup zip is empty")
            return archive, source.name + ".zip"
        except BaseException:
            archive.unlink(missing_ok=True)
            raise

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
