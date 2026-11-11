from __future__ import annotations

import unittest
from types import SimpleNamespace

from bridge.telegram.commands.backend import BackendCommands, render_backend_status


class FakeRouter:
    def __init__(self) -> None:
        self.mode = "opencode"
        self.selected: list[str] = []

    async def status(self, owner_id: str) -> dict[str, object]:
        return {
            "mode": self.mode,
            "effective": self.mode if self.mode != "auto" else "opencode",
            "freebuff": {
                "supported": True,
                "logged_in": True,
                "version": "0.0.200",
                "reason": "ok",
                "models": ["model-a", "model-b"],
            },
        }

    async def set_mode(self, owner_id: str, mode: str) -> None:
        self.mode = mode
        self.selected.append(mode)


class BackendCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_renders_backend_and_login_state(self) -> None:
        text = render_backend_status(await FakeRouter().status("1"))
        self.assertIn("Freebuff CLI", text)
        self.assertIn("0.0.200", text)
        self.assertIn("model-a", text)

    async def test_set_mode_and_reply(self) -> None:
        router = FakeRouter()
        replies: list[str] = []

        async def reply(_message, text: str) -> None:
            replies.append(text)

        commands = BackendCommands(router, reply=reply, error_message=lambda exc, op: "error")
        update = SimpleNamespace(effective_user=SimpleNamespace(id=7), message=object())
        context = SimpleNamespace(args=["freebuff"])
        await commands.handle(update, context)
        self.assertEqual(router.selected, ["freebuff"])
        self.assertTrue(any("Freebuff" in text for text in replies))

    async def test_invalid_mode_does_not_change_selection(self) -> None:
        router = FakeRouter()
        replies: list[str] = []

        async def reply(_message, text: str) -> None:
            replies.append(text)

        commands = BackendCommands(router, reply=reply, error_message=lambda exc, op: "error")
        update = SimpleNamespace(effective_user=SimpleNamespace(id=7), message=object())
        await commands.handle(update, SimpleNamespace(args=["unknown"]))
        self.assertEqual(router.selected, [])
        self.assertIn("opencode", replies[0])


if __name__ == "__main__":
    unittest.main()
