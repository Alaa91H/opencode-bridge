"""Privacy-preserving JSONL audit logging for the OpenCode Telegram bridge."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SENSITIVE_KEY_RE = re.compile(r"(?:token|secret|password|authorization|api[_-]?key)", re.IGNORECASE)


def read_recent(path: Path, *, max_bytes: int = 262144, limit: int = 2000) -> list[dict[str, Any]]:
    """Return the newest well-formed audit events from a JSONL file.

    Only the last ``max_bytes`` are read, so summarizing an unattended log
    costs a bounded seek plus one small read instead of parsing the whole
    10MB file. A partially written trailing line (the logger appends without
    holding a lock across processes) is skipped rather than raising: metrics
    are observability, never a reason to fail a command.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()  # drop the partial first line
            blob = handle.read()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for raw in blob.splitlines():
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events[-max(1, int(limit)):]


class AuditLogger:
    """Append structured, redacted events without logging user message content."""

    # Rotation cap: beyond this size the active file is shifted to `<path>.1`
    # (single generation) so an unattended host cannot fill its disk.
    MAX_BYTES = 10 * 1024 * 1024

    def __init__(self, path: Path, *, max_bytes: int = MAX_BYTES) -> None:
        self.path = path
        self.max_bytes = max(1024, int(max_bytes))

    @staticmethod
    def _sanitize(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): "<redacted>" if SENSITIVE_KEY_RE.search(str(key)) else AuditLogger._sanitize(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [AuditLogger._sanitize(item) for item in value]
        if isinstance(value, str):
            return value[:512]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:512]

    def write(
        self,
        event: str,
        outcome: str,
        *,
        actor_id: str | int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.path.is_file() and self.path.stat().st_size >= self.max_bytes:
                rotated = self.path.with_name(self.path.name + ".1")
                try:
                    if rotated.is_file() or rotated.is_symlink():
                        rotated.unlink()
                except OSError:
                    pass
                try:
                    os.replace(self.path, rotated)
                    try:
                        os.chmod(rotated, 0o640)
                    except OSError:
                        pass
                except OSError:
                    pass
        except OSError:
            pass
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "outcome": outcome,
            "actor_id": str(actor_id) if actor_id is not None else None,
            "details": self._sanitize(details or {}),
        }
        fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o640)
        try:
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            # Audit logging must never interrupt a user-facing operation.
            try:
                os.close(fd)
            except OSError:
                pass
