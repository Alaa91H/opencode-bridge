from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "maintenance" / "self_update.py"
SPEC = importlib.util.spec_from_file_location("maintenance_self_update", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
self_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(self_update)


class SelfUpdatePolicyTests(unittest.TestCase):
    def test_accepts_only_expected_github_repository_forms(self) -> None:
        expected = "alaa91h/opencode-bridge"
        self.assertEqual(
            self_update.normalize_github_repository("https://github.com/Alaa91H/opencode-bridge.git"),
            expected,
        )
        self.assertEqual(
            self_update.normalize_github_repository("git@github.com:Alaa91H/opencode-bridge.git"),
            expected,
        )
        self.assertEqual(
            self_update.normalize_github_repository("ssh://git@github.com/Alaa91H/opencode-bridge.git"),
            expected,
        )

    def test_rejects_non_github_and_wrong_repository(self) -> None:
        self.assertIsNone(self_update.normalize_github_repository("https://example.com/Alaa91H/opencode-bridge.git"))
        self.assertNotEqual(
            self_update.normalize_github_repository("https://github.com/other/opencode-bridge.git"),
            self_update.EXPECTED_REPOSITORY,
        )

    def test_update_implementation_never_uses_force_reset_or_clean(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("reset --hard", source)
        self.assertNotIn("git clean", source)
        self.assertNotIn('"merge", "--ff-only"', source)
        self.assertIn('"worktree", "add", "--detach"', source)
        self.assertIn('"checkout", "--detach", "--quiet"', source)
        self.assertIn('"fetch", "--prune", "--tags"', source)

    def test_stable_tag_pattern_rejects_prereleases(self) -> None:
        self.assertIsNotNone(self_update._STABLE_TAG.fullmatch("v1.8.2"))
        self.assertIsNotNone(self_update._STABLE_TAG.fullmatch("v2.0.0"))
        self.assertIsNone(self_update._STABLE_TAG.fullmatch("v2.0.0-rc1"))
        self.assertIsNone(self_update._STABLE_TAG.fullmatch("latest"))

    def test_untracked_parser_and_conflict_detection_are_exact(self) -> None:
        self.assertEqual(
            self_update._nul_paths("wait_for_opencode.py\0runtime/local.tmp\0"),
            {"wait_for_opencode.py", "runtime/local.tmp"},
        )
        self.assertEqual(self_update._nul_paths(""), set())

    def test_policy_ignores_nonconflicting_untracked_files_but_not_tracked_changes(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('"--untracked-files=no"', source)
        self.assertIn('"ls-files", "--others", "--exclude-standard", "-z"', source)
        self.assertIn('"ls-tree", "-r", "--name-only", "-z"', source)
        self.assertIn("untracked files would conflict with the target release", source)


if __name__ == "__main__":
    unittest.main()
