from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("maintenance_self_update", PROJECT_DIR / "maintenance" / "self_update.py")
self_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(self_update)


class SelfUpdatePolicyTests(unittest.TestCase):
    def test_repo_validation(self):
        self.assertEqual(self_update.normalize_github_repository(
            "git@github.com:Alaa91H/opencode-bridge.git"), self_update.EXPECTED_REPOSITORY)
        self.assertIsNone(self_update.normalize_github_repository("https://other.test/org/repo"))

    def test_stable_version_only(self):
        self.assertEqual(self_update.version_of("v3.2.1"), (3, 2, 1))
        self.assertIsNone(self_update.version_of("v3.2.1-rc.1"))
        self.assertIsNone(self_update.version_of("../malicious"))

    def test_tagged_success_is_required(self):
        good = {"head_sha": "a"*40, "head_branch": "v3.0.0",
                "event": "push", "status": "completed", "conclusion": "success"}
        self.assertTrue(self_update.verified_ci("a"*40, "v3.0.0",
                        api=lambda _: {"workflow_runs": [good]}))
        self.assertFalse(self_update.verified_ci("a"*40, "v3.0.1",
                         api=lambda _: {"workflow_runs": [good]}))

    def test_remote_mismatch_never_deploys(self):
        with tempfile.TemporaryDirectory() as folder:
            called = []
            def git(*args, cwd):
                return "https://github.com/attacker/bridge.git"
            output = self_update.update(root=Path(folder), git_run=git,
                                        deploy=lambda x: called.append(x))
            self.assertEqual(output["status"], "skipped")
            self.assertFalse(called)

    def test_version_ahead_does_not_downgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "VERSION").write_text("3.2.0\n", encoding="utf-8")
            called = []
            def git(*args, cwd):
                return ("https://github.com/Alaa91H/opencode-bridge.git"
                        if args[0] == "remote" else "")
            result = self_update.update(
                root=Path(folder), git_run=git,
                api=lambda url: {"tag_name": "v3.0.0", "draft": False, "prerelease": False},
                deploy=lambda cmd: called.append(cmd))
            self.assertEqual(result["status"], "up_to_date")
            self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
