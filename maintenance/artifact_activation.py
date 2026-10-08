"""Validate and atomically activate a GitHub-built application release."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?\Z")
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactActivator:
    def __init__(
        self,
        releases_root: Path,
        current: Path,
        legacy_root: Path,
        shared_runtime: Path,
        shared_database: Path,
        backup_root: Path | None = None,
    ) -> None:
        self.releases_root = releases_root
        self.current = current
        self.legacy_root = legacy_root
        self.shared_runtime = shared_runtime
        self.shared_database = shared_database
        self.backup_root = backup_root or releases_root.parent / "backups"

    def activate(
        self,
        archive: Path,
        manifest_path: Path,
        expected_sha: str,
        expected_version: str,
        *,
        prepare: Callable[[Path], None],
        restart: Callable[[], None],
        smoke: Callable[[Path], None],
        commit: Callable[[Path], None] | None = None,
        quiesce: Callable[[], None] | None = None,
    ) -> Path:
        self._verify_artifact(archive, manifest_path, expected_sha, expected_version)
        self.releases_root.mkdir(parents=True, exist_ok=True)
        self.shared_runtime.mkdir(parents=True, exist_ok=True)
        self.shared_database.parent.mkdir(parents=True, exist_ok=True)
        self.shared_database.touch(exist_ok=True)
        target = self.releases_root / expected_sha
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix=f".{expected_sha}.", dir=self.releases_root) as staging_name:
                staging = Path(staging_name)
                self._extract_safe(archive, staging)
                version_file = staging / "VERSION"
                if not version_file.is_file() or version_file.read_text(encoding="utf-8").strip() != expected_version:
                    raise ValueError("artifact VERSION does not match the requested release")
                self._link_shared_state(staging)
                (staging / "deployment-manifest.json").write_text(
                    manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                os.replace(staging, target)

        if not target.is_dir() or not (target / "VERSION").is_file():
            raise ValueError("existing release directory is incomplete")
        if (target / "VERSION").read_text(encoding="utf-8").strip() != expected_version:
            raise ValueError("existing release directory has a different version")
        for name in ("runtime", "sessions.db"):
            shared_path = target / name
            if not shared_path.is_symlink() or shared_path.resolve() != (self.shared_runtime if name == "runtime" else self.shared_database).resolve():
                raise ValueError(f"existing release has an invalid shared state path: {name}")

        previous = self._current_target()
        if previous is None:
            if not self.legacy_root.is_dir():
                raise FileNotFoundError("legacy release path is missing")
            previous = self.legacy_root.resolve()
            self._switch(previous)

        backup: Path | None = None
        switched = False
        quiesced = False
        database_may_have_changed = False
        try:
            if quiesce is not None:
                quiesce()
            quiesced = True
            backup = self._ensure_database_backup(expected_sha)
            prepare(target)
            if previous != target.resolve():
                self._switch(target)
                switched = True
            database_may_have_changed = True
            restart()
            smoke(target)
            if commit is not None:
                commit(target)
        except BaseException:
            if switched:
                self._switch(previous)
            if database_may_have_changed and backup is not None:
                self._restore_database(backup)
            try:
                restart()
                smoke(previous)
            except BaseException as rollback_error:
                raise RuntimeError("activation failed and previous release recovery failed") from rollback_error
            raise
        return target

    @staticmethod
    def _verify_artifact(archive: Path, manifest_path: Path, expected_sha: str, expected_version: str) -> None:
        if not _SHA.fullmatch(expected_sha):
            raise ValueError("expected source SHA must be a full lowercase Git commit SHA")
        if not _VERSION.fullmatch(expected_version):
            raise ValueError("expected release version is invalid")
        if archive.stat().st_size > _MAX_ARCHIVE_BYTES:
            raise ValueError("artifact exceeds the compressed size limit")
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source_sha") != expected_sha:
            raise ValueError("artifact source SHA does not match the requested commit")
        if manifest.get("version") != expected_version:
            raise ValueError("artifact version does not match the requested release")
        if manifest.get("artifact") != archive.name:
            raise ValueError("artifact filename does not match its manifest")
        if manifest.get("sha256") != _sha256_file(archive):
            raise ValueError("artifact checksum does not match its manifest")

    @staticmethod
    def _extract_safe(archive: Path, destination: Path) -> None:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            total_size = sum(member.size for member in members)
            if total_size > _MAX_ARCHIVE_BYTES:
                raise ValueError("artifact expands beyond the allowed size")
            for member in members:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts or "\\" in member.name:
                    raise ValueError("artifact contains an unsafe path")
                if not member.isdir() and not member.isfile():
                    raise ValueError("artifact contains a link or special file")
                target = destination.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("artifact path escapes its release directory")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError("artifact file content is missing")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(member.mode & 0o755)

    def _link_shared_state(self, release: Path) -> None:
        for name, source in (("runtime", self.shared_runtime), ("sessions.db", self.shared_database)):
            destination = release / name
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"artifact must not include shared state path: {name}")
            destination.symlink_to(source.resolve(), target_is_directory=name == "runtime")

    def _ensure_database_backup(self, source_sha: str) -> Path:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup = self.backup_root / f"{source_sha}-sessions.db"
        if not backup.exists():
            temporary = self.backup_root / f".{source_sha}-sessions.db.tmp"
            temporary.unlink(missing_ok=True)
            source = sqlite3.connect(self.shared_database)
            destination = sqlite3.connect(temporary)
            try:
                source.backup(destination)
                result = destination.execute("PRAGMA integrity_check").fetchone()
                if result is None or result[0] != "ok":
                    raise ValueError("SQLite deployment backup failed integrity check")
            finally:
                destination.close()
                source.close()
            try:
                os.replace(temporary, backup)
            finally:
                temporary.unlink(missing_ok=True)
        checksum_path = backup.with_suffix(".sha256")
        checksum_path.write_text(f"{_sha256_file(backup)}  {backup.name}\n", encoding="ascii")
        return backup

    def _restore_database(self, backup: Path) -> None:
        if self.shared_database.is_symlink():
            raise ValueError("refusing to replace a symlinked shared database")
        temporary = self.shared_database.with_name(f".{self.shared_database.name}.restore")
        temporary.unlink(missing_ok=True)
        source = sqlite3.connect(backup)
        destination = sqlite3.connect(temporary)
        try:
            source.backup(destination)
            result = destination.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise ValueError("database rollback backup failed integrity check")
        finally:
            destination.close()
            source.close()
        temporary.chmod(0o600)
        os.replace(temporary, self.shared_database)
        Path(f"{self.shared_database}-wal").unlink(missing_ok=True)
        Path(f"{self.shared_database}-shm").unlink(missing_ok=True)

    def _current_target(self) -> Path | None:
        if not self.current.is_symlink():
            if self.current.exists():
                raise ValueError("active release path exists but is not a symlink")
            return None
        return self.current.resolve(strict=True)

    def _switch(self, target: Path) -> None:
        self.current.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.current.with_name(f".{self.current.name}.next")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target.resolve(), target_is_directory=True)
        os.replace(temporary, self.current)
