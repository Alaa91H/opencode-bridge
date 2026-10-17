"""HostControl destructive actions share the update guard."""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.services.host_control import CommandResult, HostControl
from bridge.services.host_operations import HOST_OPERATIONS, label_for


class _Hold:
    """Holds the process-wide guard for the duration of a test."""

    def __init__(self, kind: str = "system") -> None:
        self.kind = kind
        self._task: asyncio.Task | None = None
        self._release = asyncio.Event()
        self._started = asyncio.Event()

    async def __aenter__(self) -> _Hold:
        async def operation() -> str:
            self._started.set()
            await self._release.wait()
            return "done"

        self._task = asyncio.create_task(HOST_OPERATIONS.run(self.kind, operation))
        await self._started.wait()
        return self

    async def __aexit__(self, *_exc) -> None:
        self._release.set()
        if self._task is not None:
            await self._task


class _AlwaysActive:
    """Stand-in for ``run_fixed``: exits 0 and reports the unit as active."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, argv, **kwargs) -> CommandResult:
        self.calls += 1
        return CommandResult(0, "active\n")


class HostControlGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.host = HostControl(Path("/tmp"))

    async def test_reboot_is_refused_before_any_probe(self) -> None:
        async with _Hold("system"):
            with patch("bridge.services.host_control.launch_transient") as launch:
                with patch("bridge.services.host_control.run_fixed") as runner:
                    result = await self.host.reboot()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "busy")
        self.assertEqual(result.detail, label_for("system"))
        # Not even the sudoers probe runs: the owner must not be told to retry.
        launch.assert_not_awaited()
        runner.assert_not_awaited()

    async def test_restart_is_refused_and_never_touches_the_host(self) -> None:
        async with _Hold("bridge"):
            with patch("bridge.services.host_control.run_fixed") as runner:
                result = await self.host.restart_service("opencode")
        self.assertEqual(result.code, "busy")
        self.assertEqual(result.detail, label_for("bridge"))
        runner.assert_not_awaited()

    async def test_unknown_service_key_is_still_rejected_when_busy(self) -> None:
        async with _Hold("system"):
            with self.assertRaises(ValueError):
                await self.host.restart_service("not-a-service")

    async def test_cleanup_is_refused_while_the_host_is_owned(self) -> None:
        async with _Hold("agent"):
            with patch("bridge.services.cache_retention.manual_cleanup") as cleanup:
                result = await self.host.cleanup_now()
        self.assertEqual(result.code, "busy")
        cleanup.assert_not_called()

    async def test_free_host_runs_the_action_normally(self) -> None:
        self.assertFalse(HOST_OPERATIONS.busy)
        runner = _AlwaysActive()
        with patch("bridge.services.host_control.run_fixed", new=runner):
            result = await self.host.restart_service("opencode")
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "restarted")
        self.assertGreaterEqual(runner.calls, 2)


if __name__ == "__main__":
    unittest.main()
