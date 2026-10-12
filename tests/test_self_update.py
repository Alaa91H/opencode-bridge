from __future__ import annotations

import importlib.util
import json
import subprocess
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


    def test_failed_activation_quarantines_only_the_failed_tag(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "VERSION").write_text("3.2.0\n", encoding="utf-8")
            release = {"tag": "v3.3.0"}
            commit = "a" * 40
            calls = []

            def git(*args, cwd):
                if args[0] == "remote":
                    return "https://github.com/Alaa91H/opencode-bridge.git"
                if args[0] == "status":
                    return ""
                if args[0] == "rev-parse":
                    return commit
                if args[0] == "show":
                    return release["tag"][1:]
                if args[0] == "fetch":
                    return ""
                self.fail(f"Unexpected git command: {args}")

            def api(url):
                if url == self_update.RELEASE_API:
                    return {"tag_name": release["tag"], "draft": False, "prerelease": False}
                return {"workflow_runs": [{
                    "head_sha": commit, "head_branch": release["tag"],
                    "event": "push", "status": "completed", "conclusion": "success",
                }]}

            def fail_deploy(command):
                calls.append(command)
                raise subprocess.CalledProcessError(1, command)

            first = self_update.update(root=root, git_run=git, api=api, deploy=fail_deploy)
            self.assertEqual(first["status"], "error")
            state = json.loads((root / "runtime" / self_update.QUARANTINE_FILE).read_text())
            self.assertEqual(state["tag"], "v3.3.0")
            self.assertEqual(state["commit"], commit)
            self.assertEqual(state["reason"], "CalledProcessError")

            retry = self_update.update(
                root=root, git_run=git, api=api, deploy=lambda cmd: calls.append(cmd),
            )
            self.assertEqual(retry["status"], "skipped")
            self.assertEqual(len(calls), 1, "Quarantined release must not redeploy")

            release["tag"] = "v3.4.0"
            next_version = self_update.update(
                root=root, git_run=git, api=api, deploy=lambda cmd: calls.append(cmd),
            )
            self.assertEqual(next_version["status"], "updated")
            self.assertEqual(len(calls), 2)

    def test_corrupt_quarantine_refuses_automatic_activation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "runtime").mkdir()
            (root / "runtime" / self_update.QUARANTINE_FILE).write_text(
                "not JSON", encoding="utf-8",
            )
            with self.assertRaises(json.JSONDecodeError):
                self_update.is_quarantined(root, "v3.3.0")


if __name__ == "__main__":
    unittest.main()
