"""Contract for the cumulative publish helper.

The workspace cannot run git or bash, so the script is verified structurally:
every destructive step must be preceded by its guard, and no step may force,
amend or delete anything on the remote.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish-cumulative.sh"


class PublishScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_script_exists_and_is_strict(self) -> None:
        self.assertIn("#!/usr/bin/env bash", self.text)
        self.assertIn("set -Eeuo pipefail", self.text)

    def test_branch_and_repository_are_guarded(self) -> None:
        self.assertIn('[[ -d .git ]] || fail', self.text)
        self.assertIn('== "main" ]] || fail', self.text)

    def test_local_gate_runs_before_any_push(self) -> None:
        verify = self.text.index("scripts/verify.sh")
        push = self.text.index("git push origin main\n")
        self.assertLess(verify, push, "the quality gate must run before pushing")

    def test_tag_is_never_forced_or_rewritten(self) -> None:
        for forbidden in ("push --force", "push -f", "commit --amend", "reset --hard",
                          "push --delete", "gh release delete", "git clean"):
            self.assertNotIn(forbidden, self.text)

    def test_tag_push_follows_a_green_ci_wait(self) -> None:
        wait = self.text.index("gh run list")
        release = self.text.index("scripts/release.sh")
        follow = self.text.index("--follow-tags")
        self.assertLess(wait, release)
        self.assertLess(release, follow)

    def test_failed_ci_aborts_before_tagging(self) -> None:
        self.assertRegex(self.text, r"completed:\*\)[\s\S]{0,200}fail ")

    def test_scratch_file_is_removed(self) -> None:
        self.assertIn("CHANGELOG_HEAD.tmp", self.text)

    def test_no_crlf_line_endings(self) -> None:
        self.assertNotIn("\r", self.text)

    def test_usage_declares_both_options(self) -> None:
        self.assertRegex(self.text, r"--skip-ci\)\s+SKIP_CI=1")
        self.assertRegex(self.text, r"--yes\)\s+ASSUME_YES=1")
        self.assertTrue(re.search(r"الاستخدام", self.text))


if __name__ == "__main__":
    unittest.main()
