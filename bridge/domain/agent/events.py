"""Build OpenCode-shaped events for non-OpenCode backends.

The Telegram progress pipeline already understands a small event vocabulary.
Reusing that shape keeps one rendering path instead of teaching every consumer
about a second protocol.
"""

from __future__ import annotations

import time
import uuid
from typing import Any


def status_event(session_id: str, state: str, message: str = "") -> dict[str, Any]:
    status: dict[str, Any] = {"type": state}
    if message:
        status["message"] = message
    return {
        "type": "session.status",
        "properties": {"sessionID": session_id, "status": status},
    }


def assistant_message(session_id: str, text: str, *, message_id: str | None = None) -> dict[str, Any]:
    message_id = message_id or "fb_" + uuid.uuid4().hex
    part_id = "fbp_" + uuid.uuid4().hex
    return {
        "type": "message.part.updated",
        "properties": {
            "sessionID": session_id,
            "part": {
                "id": part_id,
                "messageID": message_id,
                "sessionID": session_id,
                "type": "text",
                "text": text,
            },
        },
    }


def assistant_delta(
    session_id: str,
    delta: str,
    *,
    message_id: str,
    part_id: str,
) -> dict[str, Any]:
    return {
        "type": "message.part.delta",
        "properties": {
            "sessionID": session_id,
            "messageID": message_id,
            "partID": part_id,
            "field": "text",
            "delta": delta,
        },
    }


def idle_event(session_id: str) -> dict[str, Any]:
    return status_event(session_id, "idle", "اكتمل تنفيذ المهمة.")


def completed_epoch() -> int:
    return int(time.time())
