"""v1.8 -> 2.0 migration planning with backup-first and idempotent schedule keys."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MigrationReport:
    dry_run: bool
    backup_path: str
    sessions: int = 0
    schedules: int = 0
    pending_attachments: int = 0
    owners: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run, "backup_path": self.backup_path,
            "sessions": self.sessions, "schedules": self.schedules,
            "pending_attachments": self.pending_attachments,
            "owners": sorted(self.owners), "warnings": self.warnings,
        }


class V18MigrationPlanner:
    def __init__(self, source: Path, backup: Path) -> None:
        self.source = source
        self.backup = backup

    def create_backup(self) -> None:
        src = sqlite3.connect(str(self.source))
        dst = sqlite3.connect(str(self.backup))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    @staticmethod
    def schedule_key(owner_id: str, legacy_id: int) -> str:
        return hashlib.sha256(f"v1.8:{owner_id}:{legacy_id}".encode()).hexdigest()

    def inspect(self, *, dry_run: bool = True) -> MigrationReport:
        if not self.backup.is_file():
            raise RuntimeError("backup must exist before migration inspection")
        report = MigrationReport(dry_run=dry_run, backup_path=str(self.backup))
        db = sqlite3.connect(str(self.source))
        db.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "sessions" in tables:
                rows = list(db.execute("SELECT telegram_user_id FROM sessions"))
                report.sessions = len(rows)
                report.owners.update(str(r["telegram_user_id"]) for r in rows)
            if "scheduled_jobs" in tables:
                rows = list(db.execute("SELECT id,owner_id FROM scheduled_jobs"))
                report.schedules = len(rows)
                report.owners.update(str(r["owner_id"]) for r in rows)
                keys = [self.schedule_key(str(r["owner_id"]), int(r["id"])) for r in rows]
                if len(keys) != len(set(keys)):
                    raise RuntimeError("duplicate legacy schedule migration key")
            if "pending_attachment_batches" in tables:
                rows = list(db.execute("SELECT owner_id,attachments_json FROM pending_attachment_batches"))
                report.pending_attachments = sum(len(json.loads(r["attachments_json"] or "[]")) for r in rows)
                report.owners.update(str(r["owner_id"]) for r in rows)
        finally:
            db.close()
        return report
