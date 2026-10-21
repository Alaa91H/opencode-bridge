from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TaskArchitectureTests(unittest.TestCase):
    def test_task_and_agent_services_have_no_telegram_or_bot_imports(self) -> None:
        for relative in (
            "bridge/services/task_service.py",
            "bridge/services/agent_service.py",
        ):
            path = ROOT / relative
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots.add(node.module.split(".", 1)[0])
            self.assertNotIn("telegram", roots, relative)
            self.assertNotIn("bot", roots, relative)

    def test_task_telegram_adapter_does_not_reach_repository_directly(self) -> None:
        source = (ROOT / "bridge/telegram/commands/tasks.py").read_text(encoding="utf-8")
        self.assertNotIn("task_service.repository", source)
        self.assertNotIn("task_store", source)

    def test_legacy_task_handlers_are_thin_delegates(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        expected = {
            "cmd_abort": "abort",
            "cmd_tasks": "tasks",
            "cmd_cancel": "cancel",
            "cmd_progress": "progress",
            "cmd_trace": "trace",
            "cmd_research_mode": "research",
            "handle_message": "text",
        }
        found = set()
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name in expected:
                found.add(node.name)
                self.assertEqual(len(node.body), 1, node.name)
                statement = node.body[0]
                self.assertIsInstance(statement, ast.Expr, node.name)
                self.assertIsInstance(statement.value, ast.Await, node.name)
                call = statement.value.value
                self.assertIsInstance(call, ast.Call, node.name)
                self.assertIsInstance(call.func, ast.Attribute, node.name)
                self.assertEqual(call.func.attr, expected[node.name], node.name)
        self.assertEqual(found, set(expected))


    def test_legacy_agent_handlers_are_thin_delegates(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        expected = {
            "cmd_start": "start",
            "cmd_new": "new",
            "cmd_share": "share",
            "cmd_unshare": "unshare",
            "cmd_model": "model",
            "cmd_status": "status",
            "cmd_health": "health",
            "cmd_agents": "agents",
        }
        found = set()
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name in expected:
                found.add(node.name)
                self.assertEqual(len(node.body), 1, node.name)
                statement = node.body[0]
                self.assertIsInstance(statement, ast.Expr, node.name)
                self.assertIsInstance(statement.value, ast.Await, node.name)
                call = statement.value.value
                self.assertIsInstance(call, ast.Call, node.name)
                self.assertIsInstance(call.func, ast.Attribute, node.name)
                self.assertEqual(call.func.attr, expected[node.name], node.name)
        self.assertEqual(found, set(expected))

    def test_all_legacy_adapter_factories_referenced_by_handlers_exist(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for factory in (
            "_agent_command_adapter",
            "_task_command_adapter",
            "_schedule_command_adapter",
            "_media_adapter",
            "_reboot_callback_adapter",
            "_pending_attachment_cleanup_loop",
        ):
            self.assertIn(factory, defined)


if __name__ == "__main__":
    unittest.main()
