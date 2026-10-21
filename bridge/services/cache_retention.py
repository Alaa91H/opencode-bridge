"""Conservative UTC midnight retention used by the bot and optional user timer."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from bridge.services.download_service import DownloadService
from bridge.services.runtime_controls import RuntimeControls


def midnight_cleanup(project: Path, now: datetime | None = None) -> dict:
    return _guarded_cleanup(project, now, windowed=True)


def manual_cleanup(project: Path, now: datetime | None = None) -> dict:
    """Owner-requested cleanup: same safety guards, no midnight window.

    Only files that already passed their retention deadline are removed; the
    "everything from the previous UTC day" rule stays exclusive to the midnight run.
    """
    return _guarded_cleanup(project, now, windowed=False)


def _guarded_cleanup(project: Path, now: datetime | None, *, windowed: bool) -> dict:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("UTC cleanup requires timezone-aware current time")
    utc = now.astimezone(UTC)
    if RuntimeControls(project / "runtime").autopilot_mode() == "emergency-stop":
        return {"status": "skipped", "reason": "emergency stop is active"}
    if windowed and (utc.hour != 0 or utc.minute >= 15):
        return {"status": "skipped", "reason": "outside UTC midnight cleanup window"}

    root = project / "runtime" / "downloads"
    database = project / "sessions.db"
    if root.is_symlink() or database.is_symlink():
        raise ValueError("Unsafe cache or task-database path")
    if not root.is_dir():
        return {"status": "skipped", "reason": "no managed download cache"}
    if not database.is_file():
        return {"status": "skipped", "reason": "task database is missing"}
    if any(path.name.endswith((".part", ".working"))
           for path in root.rglob("*")):
        return {"status": "skipped", "reason": "download transfer in progress"}

    # Fail closed on an unknown or unreadable task schema; cleanup must not
    # remove a file while queued, leased, running, retrying or scheduled jobs
    # could still reference it.
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        unfinished = conn.execute(
            "SELECT COUNT(*) FROM agent_tasks WHERE status IN "
            "('queued','leased','running','retrying','scheduled')",
        ).fetchone()[0]
    if unfinished:
        return {"status": "skipped", "reason": "unfinished agent tasks"}

    service = DownloadService(root, ttl_hours=24)
    result = service.cleanup_expired(now=utc, previous_utc_day=windowed)
    return {"status": "completed", **result}
