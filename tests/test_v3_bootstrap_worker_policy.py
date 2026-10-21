from __future__ import annotations

import ast
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class V3BootstrapWorkerPolicyTests(unittest.TestCase):
    def test_v3_bootstrap_does_not_replace_production_task_service(self) -> None:
        source = (PROJECT_DIR / "run_v3.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        assignments = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "core"
                    and target.attr == "TaskService"
                ):
                    assignments.append(node.lineno)

        self.assertEqual(assignments, [])
        self.assertIn("stabilized host-resource controller", source)


if __name__ == "__main__":
    unittest.main()
