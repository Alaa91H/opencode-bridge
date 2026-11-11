"""Extract a trustworthy answer from a Freebuff run.

Primary path: the newest local chat record. Fallback: ``/export`` JSON written
into the workspace. Both are cleaned, length-bounded and redacted before the
text can reach a Telegram chat or an audit record.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from bridge.domain.tasks.live_output import redact_output
from bridge.infrastructure.freebuff.chat_store import (
    ChatMessage,
    answer_text,
    normalize_messages,
)
from bridge.infrastructure.freebuff.screen import clean_ansi

# Codebuff/Freebuff keys are not covered by the OpenCode preview redactor.
_CODEBUFF_KEY = re.compile(r"\bcb_[A-Za-z0-9_-]{10,}\b")
_ANSI_FRAGMENT = re.compile(r"\[[0-9;?]{0,12}[A-Za-z]")


def redact_secrets(text: str) -> str:
    return redact_output(_CODEBUFF_KEY.sub("[REDACTED]", text))


def clean_text(text: str, *, max_chars: int = 120_000) -> str:
    if not text:
        return ""
    value = clean_ansi(text.replace("\r\n", "\n").replace("\r", "\n"))
    value = _ANSI_FRAGMENT.sub("", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    value = redact_secrets(value)
    return value.strip()[: max(1000, max_chars)]


def answer_from_messages(messages: list[ChatMessage], *, max_chars: int = 120_000) -> str:
    return clean_text(answer_text(messages), max_chars=max_chars)


def parse_export_payload(payload: Any, *, max_chars: int = 120_000) -> str:
    messages = normalize_messages(payload)
    return answer_from_messages(messages, max_chars=max_chars)


def read_export_file(path: Path, *, max_chars: int = 120_000) -> str:
    try:
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("export file is too large")
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError("export file cannot be read") from exc
    if path.suffix.casefold() == ".json":
        try:
            return parse_export_payload(json.loads(raw), max_chars=max_chars)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("export JSON is not a supported conversation shape") from exc
    return clean_text(raw, max_chars=max_chars)


def newest_export(workspace: Path, *, since: float, max_depth: int = 2) -> Path | None:
    """Find the newest .json/.md export written by ``/export`` in the workspace."""

    root = Path(workspace)
    if not root.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    ignored = {".git", "node_modules", ".venv", "venv", "__pycache__", ".bridge"}
    try:
        root_resolved = root.resolve()
    except OSError:
        return None

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name in ignored or entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    walk(entry, depth + 1)
                    continue
                if entry.suffix.casefold() not in {".json", ".md"}:
                    continue
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_mtime < since - 0.001:
                continue
            try:
                if entry.resolve().is_relative_to(root_resolved):
                    candidates.append((stat.st_mtime, entry))
            except (OSError, ValueError):
                continue

    walk(root, 1)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def remove_quietly(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def export_age(path: Path) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
