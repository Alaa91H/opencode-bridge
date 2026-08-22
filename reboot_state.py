"""Atomic runtime state for a requested host reboot."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REQUEST_FILENAME = "reboot-request.json"
DECISION_FILENAME = "reboot-decision.json"


def request_path(runtime_dir: Path) -> Path:
    return runtime_dir / REQUEST_FILENAME


def decision_path(runtime_dir: Path) -> Path:
    return runtime_dir / DECISION_FILENAME


def read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def remove_state(path: Path) -> None:
    path.unlink(missing_ok=True)
