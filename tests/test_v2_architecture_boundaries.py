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
        }
        self.assertTrue(all(path.is_dir() for path in expected))

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
