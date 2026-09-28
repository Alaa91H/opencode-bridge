"""Progress persistence and delivery policy."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

from bridge.domain.tasks.progress import ProgressEvent, ProgressRenderer


class ProgressService:
    def __init__(self, *, persist: Callable[[ProgressEvent], Awaitable[None]],
                 edit_message: Callable[[str, str], Awaitable[None]],
                 renderer: ProgressRenderer | None = None,
                 min_interval: float = 1.0) -> None:
        self.persist = persist
        self.edit_message = edit_message
        self.renderer = renderer or ProgressRenderer()
        self.min_interval = min_interval
        self._last_sent: dict[str, float] = {}
        self._message_ids: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def bind_message(self, task_id: str, message_id: str) -> None:
        self._message_ids[task_id] = message_id

    async def publish(self, event: ProgressEvent, *, force: bool = False) -> bool:
        await self.persist(event)
        lock = self._locks.setdefault(event.task_id, asyncio.Lock())
        async with lock:
            now = monotonic()
            last = self._last_sent.get(event.task_id, float("-inf"))
            if not force and now - last < self.min_interval:
                return False
            message_id = self._message_ids.get(event.task_id)
            if message_id is None:
                return False
            await self.edit_message(message_id, self.renderer.render(event))
            self._last_sent[event.task_id] = now
            return True

    def restore_message_binding(self, task_id: str, message_id: str) -> None:
        """Rebind the persisted Telegram message after process restart."""
        self.bind_message(task_id, message_id)
