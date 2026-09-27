from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MediaArchitectureTests(unittest.TestCase):
    def test_media_service_has_no_telegram_imports(self) -> None:
        path = ROOT / "bridge" / "services" / "media_service.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertNotIn("telegram", imported)
        self.assertNotIn("bot", imported)

    def test_legacy_attachment_handlers_are_thin_delegates(self) -> None:
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        expected = {"cmd_discard", "handle_attachment"}
        found = set()
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name in expected:
                found.add(node.name)
                self.assertEqual(len(node.body), 1, node.name)
                self.assertIsInstance(node.body[0], ast.Expr, node.name)
                self.assertIsInstance(node.body[0].value, ast.Await, node.name)
        self.assertEqual(found, expected)

    def test_bot_no_longer_owns_media_group_state(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn("media_group_batches: dict", source)
        self.assertNotIn("media_group_flush_tasks: dict", source)
        adapter = (ROOT / "bridge" / "telegram" / "attachments.py").read_text(encoding="utf-8")
        self.assertIn("self.media_group_batches", adapter)
        self.assertIn("self.media_group_flush_tasks", adapter)

    def test_text_handler_delegates_pending_attachment_instruction(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        adapter = (ROOT / "bridge" / "telegram" / "commands" / "tasks.py").read_text(encoding="utf-8")
        self.assertIn("_task_command_adapter().text(update, context)", source)
        self.assertIn(
            "media_adapter.consume_pending_instruction(update, context, text)",
            adapter,
        )
        self.assertNotIn(
            "pending_records, pending_expired = await task_store.pop_pending_attachments",
            source,
        )


if __name__ == "__main__":
    unittest.main()
