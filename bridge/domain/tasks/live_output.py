"""Optional assistant-only SSE previews. Never forward user prompts, reasoning, or tool output.

OpenCode sends every role through the same event stream, so author metadata
must be verified before any text can be displayed. Raw tool results are
intentionally excluded even for an authorized Telegram chat.
"""
from __future__ import annotations

import re
from typing import Any

_SECRET = re.compile(
    r'(?im)\b((?:api[_-]?key|access[_-]?token|authorization|password|client_secret|secret_key)'  # field
    r'\s*[:=]\s*)(["\']?[^\s\r\n,"\']{8,})'
)
_BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{10,}')
# Token shapes the assistant might echo from tool output or pasted secrets.
# Mirrors the host-control patterns; replacements stay a uniform [REDACTED].
_BOT_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_GITHUB_TOKEN = re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b")
_VENDOR_KEY = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b")


def redact_output(text: str) -> str:
    """Best-effort filtering; do not confuse this with a complete DLP system."""
    redacted = _BEARER.sub('Bearer [REDACTED]', _SECRET.sub(r'\1[REDACTED]', text))
    redacted = _BOT_TOKEN.sub('[REDACTED]', redacted)
    redacted = _GITHUB_TOKEN.sub('[REDACTED]', redacted)
    return _VENDOR_KEY.sub('[REDACTED]', redacted)


class AgentLiveAssembler:
    """Deduplicate optional previews, refusing to show unverified message authors."""

    def __init__(self, *, max_tool_chars: int = 2000) -> None:
        # Retained for backwards compatibility; tool outputs are never streamed.
        self.max_tool_chars = max_tool_chars
        self._message_roles: dict[str, str] = {}
        self._part_messages: dict[str, str] = {}
        self._text_parts: dict[str, str] = {}
        self._known_types: dict[str, str] = {}
        self._untyped_deltas: dict[str, str] = {}

    def consume(self, event: dict[str, Any]) -> str:
        kind = event.get("type")
        props = event.get("properties") or {}
        if not isinstance(props, dict):
            return ""

        if kind == "message.updated":
            info = props.get("info")
            if isinstance(info, dict) and isinstance(info.get("id"), str):
                self._message_roles[info["id"]] = str(info.get("role") or "")
            return ""

        if kind == "message.part.delta":
            if props.get("field") != "text":
                return ""
            key = props.get("partID")
            chunk = props.get("delta")
            if not isinstance(key, str) or not isinstance(chunk, str):
                return ""
            message_id = self._part_messages.get(key) or props.get("messageID")
            if isinstance(message_id, str) and self._message_roles.get(message_id) not in {None, "assistant"}:
                return ""
            if key not in self._known_types:
                # Deltas can precede part metadata; never infer the part type.
                self._untyped_deltas[key] = (self._untyped_deltas.get(key, "") + chunk)[-20000:]
                return ""
            if self._known_types[key] != "text" or self._message_roles.get(self._part_messages.get(key, "")) != "assistant":
                return ""
            self._text_parts[key] = self._text_parts.get(key, "") + chunk
            return redact_output(chunk)

        if kind != "message.part.updated":
            return ""
        part = props.get("part")
        if not isinstance(part, dict) or not isinstance(part.get("id"), str):
            return ""
        key = part["id"]
        message_id = part.get("messageID") or props.get("messageID")
        if not isinstance(message_id, str) or self._message_roles.get(message_id) != "assistant":
            self._untyped_deltas.pop(key, None)
            return ""
        self._part_messages[key] = message_id
        part_type = part.get("type")
        self._known_types[key] = "synthetic" if part.get("synthetic") else str(part_type)
        if part_type != "text" or part.get("synthetic"):
            self._untyped_deltas.pop(key, None)
            return ""

        value = part.get("text")
        if not isinstance(value, str):
            return ""
        pending = self._untyped_deltas.pop(key, "")
        previous = self._text_parts.get(key, "") + pending
        if pending:
            self._text_parts[key] = previous
        if value.startswith(previous):
            self._text_parts[key] = value
            return redact_output(pending + value[len(previous):])
        # A stale or replayed snapshot must not duplicate prior text.
        return redact_output(pending)
