"""Upgrade status must reflect version, restart, and systemd state checks."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from bridge.services.server_admin import update_now


class FakePipe:
    def __init__(self, output: str) -> None:
        self.lines = iter([*(line.encode() for line in output.splitlines(keepends=True)), b""])

    async def readline(self) -> bytes:
        return next(self.lines)


class FakeProcess:
    def __init__(self, code: int, output: str) -> None:
        self.code = code
        self.stdout = FakePipe(output)
        self.returncode = None

    async def wait(self) -> int:
        self.returncode = self.code
        return self.code

    def kill(self) -> None:
        self.returncode = -9


OLD = ("opencode", "--version")
UPGRADE = ("opencode", "upgrade")
RESTART = ("systemctl", "--user", "restart", "opencode-serve.service")
ACTIVE = ("systemctl", "--user", "is-active", "opencode-serve.service")


class AgentUpgradeVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def simulate(self, results: list[tuple[int, str]]) -> tuple[str, list[tuple[str, ...]]]:
        calls: list[tuple[str, ...]] = []
        remaining = iter(results)

        async def spawn(*args, **_kwargs):
            calls.append(tuple(args))
            code, output = next(remaining)
            return FakeProcess(code, output)

        with patch("bridge.services.server_admin.asyncio.create_subprocess_exec", spawn):
            return await update_now("agent"), calls

    async def test_bridge_check_is_read_only_and_never_runs_an_upgrade(self) -> None:
        calls: list[tuple[str, ...]] = []

        async def spawn(*args, **_kwargs):
            calls.append(tuple(args))
            return FakeProcess(0, '{"status":"available","tag":"v3.3.0"}\n')

        with patch("bridge.services.server_admin.asyncio.create_subprocess_exec", spawn):
            status = await update_now("bridge-check")
        self.assertIn("Bridge update check completed (exit=0):", status)
        self.assertIn("v3.3.0", status)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][-1] == "--check")
        self.assertTrue(calls[0][-2].endswith("self_update.py"))
        self.assertFalse(any("upgrade" in arg for call in calls for arg in call))

    async def test_manual_bridge_update_uses_exact_apply_flag(self) -> None:
        calls: list[tuple[str, ...]] = []

        async def spawn(*args, **_kwargs):
            calls.append(tuple(args))
            return FakeProcess(0, '{"status":"updated","tag":"v3.3.0"}\n')

        with patch("bridge.services.server_admin.asyncio.create_subprocess_exec", spawn):
            status = await update_now("bridge")
        self.assertIn("Bridge update completed (exit=0)", status)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], "--apply")
        self.assertTrue(calls[0][-2].endswith("self_update.py"))
        self.assertFalse(any("sudo" in arg for call in calls for arg in call))

    async def test_success_requires_active_unit(self) -> None:
        status, calls = await self.simulate(
            [(0, "1.0.0\n"), (0, "updated\n"), (0, "1.1.0\n"),
             (0, ""), (0, "active\n")]
        )
        self.assertIn("upgrade verified (service active)", status)
        self.assertEqual(calls, [OLD, UPGRADE, OLD, RESTART, ACTIVE])

    async def test_broken_current_version_does_not_start_upgrade(self) -> None:
        status, calls = await self.simulate([(1, "not installed")])
        self.assertIn("not started", status)
        self.assertEqual(calls, [OLD])

    async def test_upgrade_error_does_not_restart_unit(self) -> None:
        status, calls = await self.simulate([(0, "1.0\n"), (2, "failure")])
        self.assertIn("upgrade failed (2)", status)
        self.assertEqual(calls, [OLD, UPGRADE])

    async def test_invalid_post_upgrade_version_is_not_reported_as_success(self) -> None:
        status, calls = await self.simulate(
            [(0, "1.0\n"), (0, "updated"), (1, "no version")]
        )
        self.assertIn("could not be verified", status)
        self.assertEqual(calls, [OLD, UPGRADE, OLD])

    async def test_restart_failure_is_not_reported_as_success(self) -> None:
        status, calls = await self.simulate(
            [(0, "1.0\n"), (0, "updated"), (0, "1.1\n"), (3, "failure")]
        )
        self.assertIn("service restart failed (3)", status)
        self.assertEqual(calls, [OLD, UPGRADE, OLD, RESTART])

    async def test_inactive_unit_requires_recovery(self) -> None:
        status, calls = await self.simulate(
            [(0, "1.0\n"), (0, "updated"), (0, "1.1\n"),
             (0, ""), (3, "inactive\n")]
        )
        self.assertIn("Recovery is required", status)
        self.assertNotIn("upgrade verified", status)
        self.assertEqual(calls, [OLD, UPGRADE, OLD, RESTART, ACTIVE])


if __name__ == "__main__":
    unittest.main()
