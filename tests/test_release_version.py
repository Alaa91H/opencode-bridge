from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_version.py"
SPEC = importlib.util.spec_from_file_location("release_version", MODULE_PATH)
assert SPEC and SPEC.loader
release_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_version)


class ReleaseVersionTests(unittest.TestCase):
    def test_preview_version_is_not_stable(self) -> None:
        self.assertEqual(release_version.classify("2.0.0-rc.9"), "preview")

    def test_stable_version_is_classified_for_publication(self) -> None:
        self.assertEqual(release_version.classify("1.8.2"), "stable")

    def test_malformed_version_is_rejected(self) -> None:
        for version in ("2.0.0-rc", "v1.2.3", "1.2", "1.2.3-beta.1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release_version.classify(version)


if __name__ == "__main__":
    unittest.main()
