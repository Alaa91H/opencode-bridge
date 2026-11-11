"""Read Freebuff's local conversation store defensively.

The store lives under ``<config>/projects/*/chats/*/chat-messages.json``. Its
schema is not a public contract, so parsing is shape-tolerant and capped; an
unexpected payload raises ``ValueError`` instead of silently returning partial
text. ``log.jsonl`` is never touched: the CLI has been observed writing an
account key there in plaintext on authentication errors.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_STORE_BYTES = 25 * 1024 * 1024
TEXT_PART_TYPES = {"text", "text_delta", "message", "markdown"}
SKIP_PART_TYPES = {
    "tool",
    "tool_use",
    "tool_call",
    "tool_result",
    "reasoning",
    "thinking",
    "image",
    "file",
}


@dataclass(frozen=True)
class ChatRef:
    path: Path
    chat_id: str
    project: str
    updated_at: float


@dataclass(frozen=True)
class ChatMessage:
    role: str
    text: str
    completed: bool
    message_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "completed": self.completed,
            "id": self.message_id,
        }


def chats_root(config_dir: Path) -> Path:
    return Path(config_dir) / "projects"


def iter_chats(config_dir: Path, *, limit: int = 400) -> list[ChatRef]:
    root = chats_root(config_dir)
    if not root.is_dir():
        return []
    found: list[ChatRef] = []

    def project_mtime(item: Path) -> float:
        try:
            return item.stat().st_mtime
        except OSError:
            return 0.0

    try:
        projects = sorted(root.iterdir(), key=project_mtime, reverse=True)
    except OSError:
        return []
    for project in projects[:100]:
        chats_dir = project / "chats"
        if not chats_dir.is_dir():
            continue
        try:
            chat_dirs = list(chats_dir.iterdir())
        except OSError:
            continue
        for chat in chat_dirs:
            messages = chat / "chat-messages.json"
            try:
                updated = messages.stat().st_mtime
            except OSError:
                continue
            found.append(
                ChatRef(path=chat, chat_id=chat.name, project=project.name, updated_at=updated)
            )
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
    found.sort(key=lambda item: item.updated_at, reverse=True)
    return found


def find_latest_chat(
    config_dir: Path,
    *,
    since: float | None = None,
    contains: str | None = None,
    limit: int = 400,
) -> ChatRef | None:
    """Newest chat that also contains the run's unique prompt marker.

    Concurrent Freebuff runs share one config directory, so mtime alone can pick
    another owner's conversation. The marker is the staged prompt path injected
    into this run's only user message.
    """

    for chat in iter_chats(config_dir, limit=limit):
        if since is not None and chat.updated_at < since - 0.001:
            continue
        if contains:
            try:
                messages = load_messages(chat)
            except ValueError:
                continue
            if not any(
                message.role == "user" and contains in message.text for message in messages
            ):
                continue
        return chat
    return None


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    part_type = str(block.get("type") or block.get("kind") or "").casefold()
    if part_type in SKIP_PART_TYPES:
        return ""
    if part_type and part_type not in TEXT_PART_TYPES and "text" not in block:
        return ""
    for key in ("text", "content", "value", "delta"):
        value = block.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    chunks: list[str] = []
    for key in ("parts", "blocks", "content"):
        value = item.get(key)
        if isinstance(value, list):
            chunks.extend(_block_text(block) for block in value)
        elif isinstance(value, str):
            chunks.append(value)
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def _message_role(item: dict[str, Any]) -> str:
    role = item.get("role") or item.get("sender")
    if not isinstance(role, str):
        author = item.get("author")
        role = author.get("role") if isinstance(author, dict) else ""
    normalized = str(role or "").casefold()
    if normalized in {"assistant", "agent", "ai", "model"}:
        return "assistant"
    if normalized in {"user", "human"}:
        return "user"
    if normalized in {"system", "developer"}:
        return "system"
    return normalized


def _completed(item: dict[str, Any]) -> bool:
    if item.get("completed") is True or item.get("finished") is True:
        return True
    if item.get("finishTime") or item.get("completedAt"):
        return True
    timestamp = item.get("time")
    return bool(isinstance(timestamp, dict) and timestamp.get("completed"))


def normalize_messages(payload: Any) -> list[ChatMessage]:
    if isinstance(payload, dict):
        if isinstance(payload.get("messages"), list):
            items = payload["messages"]
        elif isinstance(payload.get("items"), list):
            items = payload["items"]
        else:
            items = [value for value in payload.values() if isinstance(value, dict)]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("unsupported chat store payload")

    messages: list[ChatMessage] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        role = _message_role(item)
        text = _message_text(item).strip()
        if not text:
            continue
        messages.append(
            ChatMessage(
                role=role,
                text=text,
                completed=_completed(item),
                message_id=str(item.get("id") or item.get("messageID") or index),
            )
        )
    return messages


def load_messages(ref: ChatRef, *, max_bytes: int = MAX_STORE_BYTES) -> list[ChatMessage]:
    path = ref.path / "chat-messages.json"
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError("chat store file is missing") from exc
    if size > max_bytes:
        raise ValueError("chat store file exceeds the safe size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("chat store file is not valid JSON") from exc
    return normalize_messages(payload)


def answer_text(messages: list[ChatMessage], *, limit: int = 200_000) -> str:
    """Return the assistant text produced in the latest turn."""

    last_user = -1
    for index, message in enumerate(messages):
        if message.role == "user":
            last_user = index
    candidates = [
        message.text
        for message in messages[last_user + 1 :]
        if message.role == "assistant" and message.text.strip()
    ]
    if not candidates:
        candidates = [
            message.text
            for message in messages
            if message.role == "assistant" and message.text.strip()
        ][-1:]
    deduped: list[str] = []
    for candidate in candidates:
        if not deduped or deduped[-1] != candidate:
            deduped.append(candidate)
    return "\n\n".join(deduped)[-limit:].strip()


def newest_store_mtime(config_dir: Path) -> float:
    latest = 0.0
    for chat in iter_chats(config_dir):
        latest = max(latest, chat.updated_at)
    return latest


def safe_store_exists(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False
