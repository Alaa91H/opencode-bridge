#!/usr/bin/env python3
"""Conservative UTC midnight cleanup of Bridge-managed download cache.

Never run as root. This task is deliberately not automatically enabled until
cross-process download locking and staging verification are complete.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.services.download_service import DownloadError, DownloadService


def midnight_cleanup(project: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("UTC cleanup requires timezone-aware current time")
    utc = now.astimezone(UTC)
    if utc.hour != 0 or utc.minute >= 15:
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
    result = service.cleanup_expired(now=utc, previous_utc_day=True)
    return {"status": "completed", **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path,
                        default=Path(__file__).resolve().parents[1])
    options = parser.parse_args()
    try:
        result = midnight_cleanup(options.project)
    except (OSError, ValueError, sqlite3.Error, DownloadError):
        print(json.dumps({"status": "error", "reason": "cleanup verification failed"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
