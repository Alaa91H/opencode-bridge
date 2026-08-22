"""Privacy-preserving JSONL audit logging for the OpenCode Telegram bridge."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SENSITIVE_KEY_RE = re.compile(r"(?:token|secret|password|authorization|api[_-]?key)", re.IGNORECASE)


class AuditLogger:
    """Append structured, redacted events without logging user message content."""

    def __init__(self, path: Path) -> None:
        self.path = path

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
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
