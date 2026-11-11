"""Behaviour required from an agent execution backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from bridge.domain.agent.capabilities import BackendCapabilities


@runtime_checkable
class AgentBackend(Protocol):
    """Minimal surface the bridge needs to run one owner's tasks.

    ``ensure_session`` returns the backend conversation id, or an empty string
    when the next run should start a fresh conversation. ``send_prompt`` returns
    an OpenCode-shaped response so existing result extraction keeps working.
    """

    backend_id: str
    capabilities: BackendCapabilities

    async def health(self) -> dict[str, Any]: ...

    async def ensure_session(self, owner_id: str) -> str: ...

    async def reset_session(self, owner_id: str) -> None: ...

    async def current_model(self, owner_id: str) -> str: ...

    async def send_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        owner_id: str,
        model: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        should_continue: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    async def abort_session(self, session_id: str, owner_id: str | None = None) -> bool: ...

    async def status(self, owner_id: str) -> dict[str, Any]: ...

    def stream_events(
        self, session_id: str, owner_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]: ...

    def models(self) -> tuple[str, ...]: ...

    async def close(self) -> None: ...
