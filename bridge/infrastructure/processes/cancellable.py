"""Subprocess lifecycle with cooperative cancel and TERM-to-KILL escalation."""

from __future__ import annotations

import asyncio
import contextlib

from bridge.domain.tasks.cancellation import CancellationToken, TaskCancelled


async def terminate_process(process: asyncio.subprocess.Process, *, grace_seconds: float = 5.0) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()


async def run_cancellable_process(*argv: str, token: CancellationToken,
                                  grace_seconds: float = 5.0) -> tuple[int, bytes, bytes]:
    token.checkpoint()
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    cleanup = lambda: terminate_process(process, grace_seconds=grace_seconds)
    token.add_cleanup(cleanup)
    communicate = asyncio.create_task(process.communicate())
    cancelled = asyncio.create_task(token.wait())
    try:
        done, _ = await asyncio.wait({communicate, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if cancelled in done:
            await terminate_process(process, grace_seconds=grace_seconds)
            communicate.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await communicate
            raise TaskCancelled()
        cancelled.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancelled
        stdout, stderr = await communicate
        return process.returncode or 0, stdout, stderr
    finally:
        if process.returncode is None:
            await terminate_process(process, grace_seconds=grace_seconds)
