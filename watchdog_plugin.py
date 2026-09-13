"""Background watchdog integration for the V3 runtime."""

from __future__ import annotations

import asyncio
import logging
import os

import watchdog_runner

INTERVAL_SECONDS = max(60, int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "300")))
log = logging.getLogger("opencode_bridge.watchdog")
_task: asyncio.Task[None] | None = None
_stopped = asyncio.Event()


async def _run_loop() -> None:
    while not _stopped.is_set():
        try:
            await asyncio.to_thread(watchdog_runner.main)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("watchdog cycle failed: %s", type(exc).__name__)
        try:
            await asyncio.wait_for(_stopped.wait(), timeout=INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def install(app) -> None:
    global _task
    if _task is None:
        _stopped.clear()
        _task = asyncio.create_task(_run_loop(), name="opencode-bridge-watchdog")


async def close() -> None:
    global _task
    _stopped.set()
    if _task is None:
        return
    _task.cancel()
    await asyncio.gather(_task, return_exceptions=True)
    _task = None
