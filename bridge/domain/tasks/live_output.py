"""Merge OpenCode SSE deltas and snapshots without emitting the same text twice.

Never forward hidden reasoning. Tool inputs are deliberately excluded; tool output
may contain secrets and should only be delivered to the authorized private chat.
"""
from __future__ import annotations

import re
from typing import Any

_SECRET = re.compile(
    r'(?im)\b((?:api[_-]?key|access[_-]?token|authorization|password|client_secret|secret_key)'  # field
    r'\s*[:=]\s*)(["\']?[^\s\r\n,"\']{8,})'
)
_BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{10,}')


def redact_output(text: str) -> str:
    """Best-effort filtering; do not confuse this with a complete DLP system."""
    return _BEARER.sub('Bearer [REDACTED]', _SECRET.sub(r'\1[REDACTED]', text))


class AgentLiveAssembler:
    def __init__(self, *, max_tool_chars: int = 2000) -> None:
        self._text_parts: dict[str, str] = {}
        self._known_types: dict[str, str] = {}
        self._untyped_deltas: dict[str, str] = {}
        self._tool_states: set[tuple[str, str]] = set()
        self.max_tool_chars = max_tool_chars

    def consume(self, event: dict[str, Any]) -> str:
        kind = event.get("type")
        props = event.get("properties") or {}
        if not isinstance(props, dict):
            return ""
        if kind == "message.part.delta":
            if props.get("field") != "text":
                return ""  # excludes reasoning field
            key = props.get("partID")
            chunk = props.get("delta")
            if not isinstance(key, str) or not isinstance(chunk, str):
                return ""
            # Deltas may precede part.updated. Do not stream unknown parts:
            # reasoning parts also send field=text and MUST NOT be exposed.
            if key not in self._known_types:
                self._untyped_deltas[key] = (self._untyped_deltas.get(key, "") + chunk)[-20000:]
                return ""
            if self._known_types[key] != "text":
                return ""
            self._text_parts[key] = self._text_parts.get(key, "") + chunk
            return redact_output(chunk)
        if kind != "message.part.updated":
            return ""
        part = props.get("part")
        if not isinstance(part, dict) or not isinstance(part.get("id"), str):
            return ""
        key = part["id"]
        part_type = part.get("type")
        self._known_types[key] = str(part_type)
        if part_type != "text":
            self._untyped_deltas.pop(key, None)
        if part_type == "text" and not part.get("synthetic"):
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
            # A stale snapshot can arrive after the corresponding delta events.
            return redact_output(pending)
        if part.get("type") == "tool":
            state = part.get("state")
            if not isinstance(state, dict):
                return ""
            status = state.get("status")
            if status not in {"completed", "error"} or (key, status) in self._tool_states:
                return ""
            self._tool_states.add((key, status))
            tool = str(part.get("tool", "tool"))[:60]
            raw = state.get("output") or state.get("error") or ""
            raw = raw if isinstance(raw, str) else str(raw)
            trunc = "\n… (تم اختصار مخرجات الأداة)" if len(raw) > self.max_tool_chars else ""
            return redact_output(f"\n\n⚙️ {tool} [{status}]\n{raw[:self.max_tool_chars]}{trunc}\n")
        return ""
