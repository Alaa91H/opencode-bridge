"""SPDX inventory remains complete and bound to the exact installed lock."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.generate_runtime_sbom import generate, locked_packages

ROOT = Path(__file__).resolve().parents[1]


class RuntimeSBOMTests(unittest.TestCase):
    def test_each_locked_runtime_distribution_is_verified(self):
        packages = locked_packages((ROOT / "requirements.lock").read_text(encoding="utf-8"))
        inventory = generate(ROOT / "requirements.lock", revision="a" * 40)
        self.assertEqual(inventory["spdxVersion"], "SPDX-2.3")
        self.assertEqual(len(inventory["packages"]), len(packages))
        self.assertEqual(
            {item["name"]: item["versionInfo"] for item in inventory["packages"]},
            packages,
        )
        self.assertEqual(
            len(inventory["relationships"]), len(inventory["packages"]),
        )
        self.assertTrue(inventory["documentNamespace"].endswith("a" * 40))

    def test_lock_rejects_unpinned_and_duplicate_packages(self):
        for text in ("httpx>=0.28.0", "httpx==0.28.1\nhttpx==0.28.1",
                     "httpx==0.28.1; arbitrary_condition == 'x'"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                locked_packages(text)

    def test_environment_marker_covers_supported_versions(self):
        content = 'typing_extensions==4.16.0; python_version < "3.15"'
        self.assertEqual(
            locked_packages(content, python_version=(3, 14)),
            {"typing-extensions": "4.16.0"},
        )
        with self.assertRaises(ValueError):
            locked_packages(content, python_version=(3, 15))

    def test_installed_version_mismatch_fails_before_publishing_sbom(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "lock.txt"
            lock.write_text("httpx==0.28.1\n", encoding="utf-8")
            with patch("scripts.generate_runtime_sbom.metadata.version",
                       return_value="0.0.0"):
                with self.assertRaisesRegex(ValueError, "Installed package mismatch"):
                    generate(lock)

    def test_invalid_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid source commit"):
            generate(ROOT / "requirements.lock", revision="../untrusted")


if __name__ == "__main__":
    unittest.main()
