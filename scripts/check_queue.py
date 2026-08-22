#!/usr/bin/env python3
"""Read-only deployment guard for the persistent Telegram task queue."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    database = project / "sessions.db"
    if not database.is_file():
        print("queue_guard=database_missing")
        return 1

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) FROM agent_tasks GROUP BY status ORDER BY status"
        ).fetchall()
    summary = {str(status): int(count) for status, count in rows}
    running = summary.get("running", 0)
    print("queue_status=" + ",".join(f"{status}:{count}" for status, count in summary.items()))
    if running:
        print(f"queue_guard=blocked_running_tasks:{running}")
        return 2
    print("queue_guard=clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
