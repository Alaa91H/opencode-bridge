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

    def migrate_into(self, target: Path) -> MigrationReport:
        """Copy supported v1.8 state into an already migrated v2 database."""
        report = self.inspect(dry_run=False)
        src = sqlite3.connect(str(self.source))
        src.row_factory = sqlite3.Row
        dst = sqlite3.connect(str(target))
        try:
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "sessions" in tables:
                for row in src.execute("SELECT * FROM sessions"):
                    exists = dst.execute(
                        "SELECT 1 FROM agent_sessions WHERE owner_id=? AND provider_session_id=?",
                        (str(row["telegram_user_id"]), str(row["opencode_session_id"])),
                    ).fetchone()
                    if not exists:
                        created = row["created_at"] if "created_at" in row.keys() else ""
                        updated = row["updated_at"] if "updated_at" in row.keys() else created
                        dst.execute(
                            "INSERT INTO agent_sessions(owner_id,provider_session_id,model,title,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                            (str(row["telegram_user_id"]), str(row["opencode_session_id"]),
                             row["model"] if "model" in row.keys() else None,
                             row["title"] if "title" in row.keys() else None, created, updated),
                        )
            if "scheduled_jobs" in tables:
                for row in src.execute("SELECT * FROM scheduled_jobs"):
                    definition = {
                        "kind": "interval" if ("repeat_seconds" in row.keys() and row["repeat_seconds"]) else "once",
                        "legacy_id": int(row["id"]),
                        "migration_key": self.schedule_key(str(row["owner_id"]), int(row["id"])),
                        "prompt": row["prompt"] if "prompt" in row.keys() else "",
                        "repeat_seconds": row["repeat_seconds"] if "repeat_seconds" in row.keys() else None,
                    }
                    dst.execute(
                        "INSERT OR IGNORE INTO schedules(owner_id,name,definition_json,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                        (str(row["owner_id"]), str(row["name"]), json.dumps(definition, sort_keys=True),
                         int(row["enabled"]) if "enabled" in row.keys() else 1,
                         row["created_at"] if "created_at" in row.keys() else "",
                         row["updated_at"] if "updated_at" in row.keys() else ""),
                    )
            if "pending_attachment_batches" in tables:
                for row in src.execute("SELECT * FROM pending_attachment_batches"):
                    columns = row.keys()
                    dst.execute(
                        """INSERT OR REPLACE INTO pending_attachment_batches
                           (owner_id,chat_id,attachments_json,created_at,updated_at,expires_at)
                           VALUES(?,?,?,?,?,?)""",
                        (str(row["owner_id"]), int(row["chat_id"]) if "chat_id" in columns else 0,
                         row["attachments_json"], row["created_at"] if "created_at" in columns else "",
                         row["updated_at"] if "updated_at" in columns else "",
                         row["expires_at"] if "expires_at" in columns else ""),
                    )
            dst.commit()
        finally:
            dst.close()
            src.close()
        return report
