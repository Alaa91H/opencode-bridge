"""Pin CI actions to audited immutable commits; reject mutable action refs."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
ACTION_SHAS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "6d4fd22b20465ccc0f7354476588e8df7c9ca0e0",  # v7.1.0, reviewed 2026-11-11
    "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",
}


class CISupplyChainTests(unittest.TestCase):
    def test_all_workflow_actions_use_approved_commit_pins(self):
        files = list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(files, "No GitHub workflow definitions found")
        encountered = set()
        for file in files:
            text = file.read_text(encoding="utf-8")
            references = re.findall(r"^\s*-\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE)
            with self.subTest(workflow=file.name):
                self.assertTrue(references, "Workflows must pin audited actions")
                for reference in references:
                    name, separator, commit = reference.rpartition("@")
                    self.assertEqual(separator, "@", "Expected an immutable action ref")
                    self.assertIn(name, ACTION_SHAS, "Unreviewed external action")
                    self.assertRegex(commit, r"\A[0-9a-f]{40}\Z")
                    self.assertEqual(commit, ACTION_SHAS[name],
                                     "Action pin changed without review")
                    encountered.add(name)
        self.assertEqual(encountered, set(ACTION_SHAS))


if __name__ == "__main__":
    unittest.main()
