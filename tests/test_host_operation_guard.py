"""Destructive host operations are serialized; a second tap is refused."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from bridge.services import server_admin
from bridge.services.host_operations import (
    BUSY_MESSAGE,
    HOST_OPERATIONS,
    HostOperationGuard,
    label_for,
)


class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_operation_is_refused_while_one_runs(self) -> None:
        guard = HostOperationGuard()
        started = asyncio.Event()
        release = asyncio.Event()
        results: list[str] = []

        async def slow() -> str:
            started.set()
            await release.wait()
            return "done"

        async def second() -> str:
            results.append(await guard.run("reboot", lambda: asyncio.sleep(0, "other")))
            return "other"

        first = asyncio.create_task(guard.run("system", slow))
        await started.wait()
        self.assertTrue(guard.busy)
        self.assertEqual(guard.current, label_for("system"))
        await second()
        release.set()
        await first
        self.assertEqual(len(results), 1)
        self.assertIn(label_for("system"), results[0])
        self.assertFalse(guard.busy)
        self.assertIsNone(guard.current)

    async def test_guard_is_released_when_the_operation_raises(self) -> None:
        guard = HostOperationGuard()

        async def boom() -> str:
            raise RuntimeError("apt exploded")

        with self.assertRaises(RuntimeError):
            await guard.run("system", boom)
        self.assertFalse(guard.busy)
        # A failed update must not block the owner's next attempt.
        self.assertEqual(await guard.run("agent", lambda: asyncio.sleep(0, "ok")), "ok")

    async def test_a_second_operation_is_refused_not_interleaved(self) -> None:
        guard = HostOperationGuard()
        order: list[str] = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def make(name: str):
            async def run() -> str:
                order.append(f"start:{name}")
                started.set()
                await release.wait()
                order.append(f"end:{name}")
                return name

            return run

        first = asyncio.create_task(guard.run("agent", await make("a")))
        await started.wait()
        # The guard refuses instead of queueing: the second operation must never
        # run, and its caller gets the explanation as the result.
        refused = await guard.run("bridge", await make("b"))
        release.set()
        await first
        self.assertEqual(order, ["start:a", "end:a"])
        self.assertIn(label_for("agent"), refused)

    def test_labels_cover_every_gated_kind(self) -> None:
        for kind in ("agent", "system", "bridge", "bridge-check",
                     "reboot", "svcrestart", "cleanup"):
            self.assertNotEqual(label_for(kind), kind)
        self.assertEqual(label_for("unknown-thing"), "unknown-thing")


class UpdateNowTests(unittest.IsolatedAsyncioTestCase):
    def test_unknown_kind_is_still_rejected(self) -> None:
        with self.assertRaises(ValueError):
            asyncio.run(server_admin.update_now("rm-rf"))

    async def test_busy_host_refuses_a_second_update(self) -> None:
        release = asyncio.Event()
        started = asyncio.Event()

        async def hold() -> str:
            started.set()
            await release.wait()
            return "done"

        holder = asyncio.create_task(HOST_OPERATIONS.run("system", hold))
        await started.wait()
        try:
            message = await server_admin.update_now("agent")
        finally:
            release.set()
            await holder
        self.assertIn(label_for("system"), message)
        self.assertIn("جارية", message)
        self.assertFalse(HOST_OPERATIONS.busy)

    async def test_free_host_runs_the_real_operation(self) -> None:
        with patch.object(server_admin, "_run_update", return_value="updated") as runner:
            self.assertEqual(await server_admin.update_now("bridge"), "updated")
        runner.assert_awaited_once()
        self.assertEqual(runner.await_args.args[0], "bridge")

    def test_busy_message_is_owner_readable(self) -> None:
        rendered = BUSY_MESSAGE.format(current=label_for("system"))
        self.assertNotIn("{current}", rendered)
        self.assertIn(label_for("system"), rendered)


if __name__ == "__main__":
    unittest.main()
