"""Async PTY process runner for the interactive Freebuff TUI.

Only the Python standard library is used. The runner keeps the child on its own
session/process group so cancellation can reach every descendant, and reads the
master side from a daemon thread into an asyncio queue. PTY support is Unix-only;
construction raises ``RuntimeError`` elsewhere, and callers raise a clear
backend-unavailable error instead of trying to fake a terminal.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from pathlib import Path

PTY_SUPPORTED = os.name != "nt"

if PTY_SUPPORTED:  # pragma: no cover - exercised on Linux CI
    import fcntl
    import pty
    import select
    import struct
    import termios


def _set_window_size(fd: int, rows: int, cols: int) -> None:
    if not PTY_SUPPORTED:
        return
    packed = struct.pack("HHHH", max(2, rows), max(20, cols), 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


class PtyProcess:
    """One child attached to a pseudo-terminal, with process-group control."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        master_fd: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.process = process
        self.master_fd = master_fd
        self.loop = loop
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    @classmethod
    async def start(
        cls,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        cols: int = 140,
        rows: int = 45,
    ) -> PtyProcess:
        if not PTY_SUPPORTED:
            raise RuntimeError("PTY execution is only supported on Unix hosts")
        loop = asyncio.get_running_loop()
        master_fd, slave_fd = pty.openpty()
        _set_window_size(slave_fd, rows, cols)
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                argv,
                cwd=str(cwd),
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        except BaseException:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        os.set_blocking(master_fd, False)
        return cls(process, master_fd, loop)

    def _read_loop(self) -> None:  # pragma: no cover - thread body
        while not self._closed:
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                chunk = os.read(self.master_fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not chunk:
                break
            try:
                self.loop.call_soon_threadsafe(self._push, chunk)
            except RuntimeError:
                break

    def _push(self, chunk: bytes) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return self.process.poll()

    async def read(self, timeout: float) -> bytes:
        """Return the next chunk, or ``b""`` when nothing arrived in time."""

        if self._closed and self._queue.empty():
            return b""
        try:
            return await asyncio.wait_for(self._queue.get(), max(0.05, timeout))
        except TimeoutError:
            return b""

    async def write(self, text: str) -> None:
        if self._closed or not self.alive:
            return
        payload = text.encode("utf-8", errors="replace")
        await self.loop.run_in_executor(None, self._write_all, payload)

    def _write_all(self, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            try:
                written = os.write(self.master_fd, payload[offset:])
            except OSError:
                return
            if written <= 0:
                return
            offset += written

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(os.getpgid(self.process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.process.send_signal(sig)
            except (ProcessLookupError, OSError):
                pass

    async def terminate(self, grace: float = 2.0) -> None:
        if not self.alive:
            self._close()
            return
        self._signal_group(signal.SIGTERM)
        deadline = self.loop.time() + max(0.1, grace)
        while self.alive and self.loop.time() < deadline:
            await asyncio.sleep(0.1)
        if self.alive:
            self._signal_group(signal.SIGKILL)
            try:
                await asyncio.wait_for(asyncio.to_thread(self.process.wait), 3.0)
            except (TimeoutError, OSError):
                pass
        self._close()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        try:
            self.process.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass
