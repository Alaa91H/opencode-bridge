"""Atomic persisted, globally enforced owner-controlled concurrency (1 or 2).

A single bridge instance owns the task queue. The file only restricts claim
admission; it never increases the two-worker hard ceiling.
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

_LOCK = Lock()


class WorkerCapacity:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self) -> int:
        try:
            data = self.path.read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            return 1
        return 2 if data == "2" else 1

    def set(self, count: int) -> int:
        if type(count) is not int or count not in (1, 2):
            raise ValueError("Worker capacity must be 1 or 2")
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            try:
                temporary.write_text(f"{count}\n", encoding="ascii")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return count
