"""Framework-independent notification application service."""

from __future__ import annotations

from typing import Any, Protocol


class NotificationPort(Protocol):
    async def send_text(self, destination: Any, text: str) -> Any: ...


class NotificationService:
    """Deliver application notifications through an injected transport port."""

    def __init__(self, transport: NotificationPort) -> None:
        self.transport = transport

    async def send(self, destination: Any, text: str) -> Any:
        if not text:
            raise ValueError("notification text must not be empty")
        return await self.transport.send_text(destination, text)
