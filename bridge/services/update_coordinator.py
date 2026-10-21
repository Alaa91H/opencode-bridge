"""Durable, fail-closed state ledger for verified Bridge release operations.

No subprocesses, service controls or secret values live in this module.
The release updater is the only writer and owns deployment decisions.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_TAG = re.compile(r"v[0-9]+[.][0-9]+[.][0-9]+\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_NEXT = {
    "verified": frozenset({"available", "applying", "quarantined"}),
    "available": frozenset({"verified", "applying", "quarantined"}),
    "applying": frozenset({"healthy", "quarantined"}),
    "quarantined": frozenset(),
    "healthy": frozenset(),
}
_SAFETY_LIMIT = 80


@dataclass(frozen=True)
class UpdateOperation:
    tag: str
    commit: str
    phase: str
    updated_utc: str


class UpdateCoordinator:
    """SQLite WAL-backed operation journal with validated atomic transitions.

    An interrupted 'applying' operation remains blocked on restart until
    diagnosed; merely restarting the daemon cannot retrigger its deployment.
    """

    def __init__(self, runtime_dir: Path) -> None:
        self.directory = Path(runtime_dir)
        self.path = self.directory / "update-operations.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        if self.directory.is_symlink():
            raise ValueError("unsafe update journal directory")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_stat = self.directory.lstat()
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise ValueError("invalid update journal directory")
        if os.name == "posix" and (
            directory_stat.st_uid != os.geteuid()
            or directory_stat.st_mode & 0o022
        ):
            raise ValueError("insecure update journal directory ownership or permissions")

        # SQLite writes associated WAL and SHM files. A link, hard link or
        # device at any one of these locations must block activation entirely.
        for path in (self.path, self.path.with_name(self.path.name + "-wal"),
                     self.path.with_name(self.path.name + "-shm")):
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("unsafe update journal or sidecar file")
            if os.name == "posix" and metadata.st_uid != os.geteuid():
                raise ValueError("update journal file is owned by another user")
            if path != self.path and metadata.st_mode & 0o077:
                raise ValueError("update journal sidecar is not private")
        try:
            fd = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(fd)
        descriptor = os.open(self.path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            final_stat = os.fstat(descriptor)
            if not stat.S_ISREG(final_stat.st_mode) or final_stat.st_nlink != 1:
                raise ValueError("unsafe update journal descriptor")
            if os.name == "posix":
                if final_stat.st_uid != os.geteuid():
                    raise ValueError("update journal descriptor has another owner")
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS release_operations (
                    commit_sha TEXT PRIMARY KEY,
                    tag TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS release_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    commit_sha TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    created_utc TEXT NOT NULL
                )
            """)
        except BaseException:
            connection.close()
            raise
        return connection

    @staticmethod
    def _validate(tag: str, commit: str, phase: str) -> None:
        if not isinstance(tag, str) or not _TAG.fullmatch(tag):
            raise ValueError("invalid release tag")
        if not isinstance(commit, str) or not _SHA.fullmatch(commit):
            raise ValueError("invalid release commit")
        if phase not in _NEXT:
            raise ValueError("invalid update phase")

    def transition(self, tag: str, commit: str, phase: str) -> UpdateOperation:
        self._validate(tag, commit, phase)
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT tag, phase, updated_utc FROM release_operations WHERE commit_sha = ?",
                (commit,),
            ).fetchone()
            if phase == "applying":
                other = db.execute(
                    "SELECT commit_sha FROM release_operations "
                    "WHERE phase = ? AND commit_sha != ? LIMIT 1",
                    ("applying", commit),
                ).fetchone()
                if other is not None:
                    raise ValueError("another release activation needs recovery")
            if prior is None:
                if phase != "verified":
                    raise ValueError("release must first pass verification")
                db.execute(
                    "INSERT INTO release_operations VALUES (?, ?, ?, ?)",
                    (commit, tag, phase, stamp),
                )
            else:
                if prior["tag"] != tag:
                    raise ValueError("commit already recorded with a different release tag")
                if prior["phase"] == phase:
                    if phase == "applying":
                        # A second concurrent updater must never gain a lease.
                        # A restart after a crash also cannot resume blindly.
                        raise ValueError("release activation already claimed")
                    return UpdateOperation(tag, commit, phase, prior["updated_utc"])
                if phase not in _NEXT[prior["phase"]]:
                    raise ValueError("unsafe update transition")
                db.execute(
                    "UPDATE release_operations SET phase = ?, updated_utc = ? "
                    "WHERE commit_sha = ? AND phase = ?",
                    (phase, stamp, commit, prior["phase"]),
                )
            db.execute(
                "INSERT INTO release_events(commit_sha, phase, created_utc) VALUES (?, ?, ?)",
                (commit, phase, stamp),
            )
        return UpdateOperation(tag, commit, phase, stamp)

    def reconcile_interrupted(self, deployed_commit: str | None) -> UpdateOperation | None:
        """Resolve an ``applying`` record left behind by an updater that was killed.

        ``deploy.sh`` writes ``runtime/deployed-ref`` only after the new release passed
        its service and health checks. If that reference equals the interrupted commit the
        deployment finished and only the final ledger write was lost -> ``healthy``.
        Anything else means the deployment failed or died half-way -> ``quarantined``, so
        the release is never retried blindly. Returns the resolved operation, or ``None``
        when nothing was pending.
        """
        if not self.path.exists():
            return None
        with closing(self._connect()) as db, db:
            pending = db.execute(
                "SELECT tag, commit_sha FROM release_operations WHERE phase = ? "
                "ORDER BY updated_utc DESC, rowid DESC LIMIT 1", ("applying",),
            ).fetchone()
        if pending is None:
            return None
        finished = (isinstance(deployed_commit, str) and _SHA.fullmatch(deployed_commit) is not None
                    and deployed_commit == pending["commit_sha"])
        return self.transition(pending["tag"], pending["commit_sha"],
                               "healthy" if finished else "quarantined")

    def latest(self) -> UpdateOperation | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as db, db:
            record = db.execute(
                "SELECT tag, commit_sha, phase, updated_utc FROM release_operations "
                "ORDER BY updated_utc DESC, rowid DESC LIMIT 1",
            ).fetchone()
        return (UpdateOperation(record["tag"], record["commit_sha"], record["phase"],
                                record["updated_utc"]) if record else None)

    def history(self, limit: int = 10) -> list[tuple[str, str, str]]:
        if not isinstance(limit, int) or not 1 <= limit <= _SAFETY_LIMIT:
            raise ValueError("invalid history limit")
        if not self.path.exists():
            return []
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT commit_sha, phase, created_utc FROM release_events "
                "ORDER BY event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(str(r["commit_sha"])[:12], r["phase"], r["created_utc"]) for r in rows]


def write_quarantine(runtime_dir: Path, tag: str, commit: str, reason: str) -> None:
    """Persist the same schema-1 quarantine record the release updater reads.

    Atomic, private and free of logs or secrets. A symlinked destination is refused.
    """
    import json
    import tempfile

    UpdateCoordinator._validate(tag, commit, "quarantined")
    target = Path(runtime_dir) / "auto-update-quarantine.json"
    if target.is_symlink():
        raise ValueError("unsafe quarantine file")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"schema": 1, "tag": tag, "commit": commit,
               "failed_at": datetime.now(UTC).isoformat(), "reason": reason[:80]}
    fd, name = tempfile.mkstemp(prefix=".update-quarantine-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
