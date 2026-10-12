"""Systemd observability never executes user-provided commands."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from bridge.services.service_monitor import service_overview, unit_status


class FakeUnitProcess:
    def __init__(self, text: str) -> None:
        self.text = text
        self.returncode = 0

    async def communicate(self):
        return self.text.encode("ascii"), b""


class ServiceMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_fixed_systemd_units(self):
        calls = []

        async def spawn(*args, **kwargs):
            calls.append(tuple(args))
            return FakeUnitProcess("active\n")

        with patch("bridge.services.service_monitor.asyncio.create_subprocess_exec", spawn):
            states = await service_overview()
        self.assertEqual(len(states), 3)
        self.assertTrue(all(state.status == "active" for state in states))
        self.assertEqual(
            calls,
            [
                ("systemctl", "--user", "is-active", "opencode-serve.service"),
                ("systemctl", "--user", "is-active", "opencode-bridge-telegram.service"),
                ("systemctl", "is-active", "opencode-bridge-maintenance.timer"),
            ],
        )

    async def test_rejects_arbitrary_systemd_unit(self):
        with self.assertRaises(ValueError):
            await unit_status("../../danger.service", user=True)

    async def test_missing_systemd_is_reported_without_crashing(self):
        async def unavailable(*_args, **_kwargs):
            raise FileNotFoundError("systemctl not present")

        with patch("bridge.services.service_monitor.asyncio.create_subprocess_exec", unavailable):
            state = await unit_status("opencode-serve.service", user=True)
        self.assertEqual(state.status, "unavailable")

    async def test_unknown_status_is_never_rendered_as_raw_output(self):
        async def spawn(*_args, **_kwargs):
            return FakeUnitProcess("unexpected-host-data")

        with patch("bridge.services.service_monitor.asyncio.create_subprocess_exec", spawn):
            state = await unit_status("opencode-serve.service", user=True)
        self.assertEqual(state.status, "unavailable")


if __name__ == "__main__":
    unittest.main()
