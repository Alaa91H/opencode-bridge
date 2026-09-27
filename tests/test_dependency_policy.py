from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DependencyPolicyTests(unittest.TestCase):
    def test_runtime_requirements_are_exact_stable_pins(self) -> None:
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        pins: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            self.assertIn("==", line)
            name, version = line.split("==", 1)
            pins[name] = version
            self.assertRegex(version, r"^\d+(?:\.\d+){1,2}$")
            self.assertNotRegex(version.lower(), r"(?:a|b|rc|dev|alpha|beta)")

        self.assertEqual(
            pins,
            {
                "python-telegram-bot": "22.8",
                "httpx": "0.28.1",
                "aiosqlite": "0.22.1",
            },
        )

    def test_input_and_constraint_files_keep_stable_release_lines(self) -> None:
        requirements_in = (ROOT / "requirements.in").read_text(encoding="utf-8")
        constraints = (ROOT / "constraints.txt").read_text(encoding="utf-8")
        expected = {
            "python-telegram-bot": (">=22.8", "<23"),
            "httpx": (">=0.28.1", "<1"),
            "aiosqlite": (">=0.22.1", "<0.23"),
        }
        for name, fragments in expected.items():
            for text in (requirements_in, constraints):
                line = next(
                    (
                        item.strip()
                        for item in text.splitlines()
                        if item.strip().startswith(name)
                    ),
                    "",
                )
                self.assertTrue(line, f"missing {name}")
                for fragment in fragments:
                    self.assertIn(fragment, line)

    def test_ci_covers_supported_python_matrix_and_deprecation_warnings(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn('python-version: ["3.12", "3.13", "3.14"]', workflow)
        self.assertIn("python-version: ${{ matrix.python-version }}", workflow)
        self.assertIn("PYTHONWARNINGS: error::DeprecationWarning", workflow)
        self.assertIn("-r requirements.txt -c constraints.txt", workflow)

    def test_dependabot_monitors_all_current_dependency_ecosystems(self) -> None:
        config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        ecosystems = set(re.findall(r'package-ecosystem:\s*"([^"]+)"', config))
        self.assertEqual(ecosystems, {"pip", "npm", "github-actions"})
        self.assertGreaterEqual(config.count('interval: "weekly"'), 3)


if __name__ == "__main__":
    unittest.main()
