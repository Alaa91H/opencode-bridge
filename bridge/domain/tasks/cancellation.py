"""Cancellation primitives shared across task execution stages."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class TaskCancelled(asyncio.CancelledError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._callbacks: list[Callable[[], Awaitable[None] | None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def add_cleanup(self, callback: Callable[[], Awaitable[None] | None]) -> None:
        self._callbacks.append(callback)

    async def cancel(self) -> None:
        if self.cancelled:
            return
        self._event.set()
        for callback in reversed(self._callbacks):
            result = callback()
            if result is not None:
                await result

    def checkpoint(self) -> None:
        if self.cancelled:
            raise TaskCancelled()

    async def wait(self) -> None:
        await self._event.wait()
        raise TaskCancelled()
