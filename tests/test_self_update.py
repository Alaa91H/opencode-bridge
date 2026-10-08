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
        self.assertEqual(self_update.normalize_github_repository("https://github.com/Alaa91H/opencode-bridge.git"), expected)
        self.assertEqual(self_update.normalize_github_repository("git@github.com:Alaa91H/opencode-bridge.git"), expected)
        self.assertEqual(self_update.normalize_github_repository("ssh://git@github.com/Alaa91H/opencode-bridge.git"), expected)

    def test_rejects_non_github_and_wrong_repository(self) -> None:
        self.assertIsNone(self_update.normalize_github_repository("https://example.com/Alaa91H/opencode-bridge.git"))
        self.assertNotEqual(self_update.normalize_github_repository("https://github.com/other/opencode-bridge.git"), self_update.EXPECTED_REPOSITORY)

    def test_stable_tag_pattern_rejects_prereleases(self) -> None:
        self.assertIsNotNone(self_update._STABLE_TAG.fullmatch("v1.8.2"))
        self.assertIsNotNone(self_update._STABLE_TAG.fullmatch("v2.0.0"))
        self.assertIsNone(self_update._STABLE_TAG.fullmatch("v2.0.0-rc1"))
        self.assertIsNone(self_update._STABLE_TAG.fullmatch("latest"))

    def test_production_updater_never_compiles_or_runs_tests(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn('"compileall"', source)
        self.assertNotIn('"unittest"', source)
        self.assertNotIn("_validate_candidate", source)

    def test_deploy_script_does_not_run_production_validation_suite(self) -> None:
        source = (PROJECT_DIR / "scripts" / "deploy.sh").read_text(encoding="utf-8")
        self.assertNotIn("scripts/verify.sh", source)

    def test_update_preserves_clean_checkout_and_exact_sha_guards(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('"--untracked-files=no"', source)
        self.assertIn('"checkout", "--detach", "--quiet"', source)
        self.assertIn('"merge-base", "--is-ancestor"', source)
        self.assertIn('"scripts/check_queue.py"', source)


if __name__ == "__main__":
    unittest.main()
