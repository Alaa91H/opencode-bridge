from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge"


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_domain_never_imports_telegram_or_legacy_bot(self) -> None:
        for path in (BRIDGE / "domain").rglob("*.py"):
            roots = imported_roots(path)
            self.assertNotIn("telegram", roots, path)
            self.assertNotIn("bot", roots, path)

    def test_services_never_import_telegram_or_legacy_bot(self) -> None:
        for path in (BRIDGE / "services").rglob("*.py"):
            roots = imported_roots(path)
            self.assertNotIn("telegram", roots, path)
            self.assertNotIn("bot", roots, path)

    def test_new_architecture_exists_with_expected_top_level_boundaries(self) -> None:
        expected = {
            BRIDGE / "telegram",
            BRIDGE / "domain",
            BRIDGE / "services",
            BRIDGE / "infrastructure",
            BRIDGE / "workers",
            BRIDGE / "config",
            BRIDGE / "domain" / "tasks",
            BRIDGE / "domain" / "schedules",
            BRIDGE / "domain" / "attachments",
            BRIDGE / "domain" / "sessions",
            BRIDGE / "domain" / "policies",
            BRIDGE / "infrastructure" / "database",
            BRIDGE / "infrastructure" / "opencode",
            BRIDGE / "infrastructure" / "telegram",
            BRIDGE / "infrastructure" / "storage",
            BRIDGE / "infrastructure" / "metrics",
        }
        self.assertTrue(all(path.is_dir() for path in expected))

    def test_telegram_command_adapters_contain_no_sql(self) -> None:
        forbidden = ("SELECT ", "INSERT INTO ", "UPDATE ", "CREATE TABLE ", "ALTER TABLE ")
        for path in (BRIDGE / "telegram" / "commands").rglob("*.py"):
            source = path.read_text(encoding="utf-8").upper()
            for token in forbidden:
                self.assertNotIn(token, source, path)

    def test_opencode_layers_do_not_import_telegram(self) -> None:
        for relative in (
            "opencode_client.py",
            "bridge/services/agent_service.py",
            "bridge/services/task_execution_service.py",
        ):
            path = ROOT / relative
            roots = imported_roots(path)
            self.assertNotIn("telegram", roots, relative)
            self.assertNotIn("bot", roots, relative)

    def test_all_legacy_bot_handlers_are_thin_delegates(self) -> None:
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        handler_names = {
            node.name
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and (node.name.startswith("cmd_") or node.name.startswith("handle_"))
        }
        self.assertTrue(handler_names)
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef) or node.name not in handler_names:
                continue
            self.assertEqual(len(node.body), 1, node.name)
            self.assertIsInstance(node.body[0], ast.Expr, node.name)
            self.assertIsInstance(node.body[0].value, ast.Await, node.name)

    def test_compatibility_plugins_keep_handlers_thin(self) -> None:
        for relative in ("v3_plugin.py", "ci_plugin.py", "resource_commands.py"):
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                if not (node.name.startswith("cmd_") or node.name.startswith("handle_")):
                    continue
                self.assertEqual(len(node.body), 1, f"{relative}:{node.name}")
                self.assertIsInstance(node.body[0], ast.Expr, f"{relative}:{node.name}")
                self.assertIsInstance(node.body[0].value, ast.Await, f"{relative}:{node.name}")

    def test_schedule_handlers_in_bot_are_thin_delegates(self) -> None:
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        names = {
            "cmd_schedules",
            "cmd_schedule",
            "cmd_repeat",
            "cmd_schedshow",
            "cmd_schedrename",
            "cmd_schededit",
            "cmd_schedappend",
            "cmd_schedtime",
            "cmd_schedinterval",
            "cmd_schedpause",
            "cmd_schedresume",
            "cmd_scheddelete",
            "cmd_schedrun",
        }
        for node in tree.body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in names:
                # Handler body should be exactly one delegated await expression.
                self.assertEqual(len(node.body), 1, node.name)
                self.assertIsInstance(node.body[0], ast.Expr, node.name)
                self.assertIsInstance(node.body[0].value, ast.Await, node.name)


if __name__ == "__main__":
    unittest.main()
