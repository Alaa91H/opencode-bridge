"""Regression tests for tag-only rollback and its recorded SHA compatibility."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "rollback.sh"


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "git and bash required")
class RollbackTagResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "runtime").mkdir()
        shutil.copy2(SOURCE, self.root / "scripts" / "rollback.sh")
        (self.root / "scripts" / "deploy.sh").write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$1" > "$ROLLBACK_TEST_OUTPUT"\n',
            encoding="utf-8",
        )
        self.invocation = self.root / "deploy-argument"
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Rollback test")
        self.old = self.commit_release("1.0.0")
        self.new = self.commit_release("2.0.0")
        self.previous_ref = self.root / "runtime" / "previous-deployed-ref"
        self.previous_ref.write_text(self.old + "\n", encoding="ascii")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()

    def commit_release(self, version: str, *, tag: bool = True) -> str:
        (self.root / "VERSION").write_text(version + "\n", encoding="ascii")
        self.git("add", "VERSION")
        self.git("commit", "-qm", f"release {version}")
        sha = self.git("rev-parse", "HEAD")
        if tag:
            self.git("tag", "v" + version)
        return sha

    def rollback(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, ROLLBACK_TEST_OUTPUT=str(self.invocation))
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "rollback.sh"), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_recorded_previous_sha_resolves_to_its_exact_release_tag(self) -> None:
        result = self.rollback()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.invocation.read_text(encoding="utf-8").strip(), "v1.0.0")

    def test_explicit_full_sha_resolves_to_tag(self) -> None:
        result = self.rollback(self.old)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.invocation.read_text(encoding="utf-8").strip(), "v1.0.0")

    def test_explicit_matching_version_tag_is_accepted(self) -> None:
        result = self.rollback("v1.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.invocation.read_text(encoding="utf-8").strip(), "v1.0.0")

    def test_missing_release_tag_is_rejected_without_deployment(self) -> None:
        self.git("tag", "-d", "v1.0.0")
        result = self.rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.invocation.exists())

    def test_retargeted_tag_is_rejected_without_deployment(self) -> None:
        self.git("tag", "-f", "v1.0.0", self.new)
        result = self.rollback(self.old)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.invocation.exists())

    def test_wrong_tag_for_version_is_rejected(self) -> None:
        self.git("tag", "v1.0.1", self.old)
        result = self.rollback("v1.0.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.invocation.exists())

    def test_rev_expressions_and_branches_are_not_accepted(self) -> None:
        for target in ("HEAD", "HEAD~1", "main", self.old[:12], "--help", "v1.0.0^{commit}"):
            with self.subTest(target=target):
                result = self.rollback(target)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertFalse(self.invocation.exists())

    def test_already_deployed_release_does_not_restart(self) -> None:
        (self.root / "runtime" / "deployed-ref").write_text(self.old + "\n", encoding="ascii")
        result = self.rollback(self.old)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.invocation.exists())

    def test_missing_previous_reference_fails_closed(self) -> None:
        self.previous_ref.unlink()
        result = self.rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.invocation.exists())


if __name__ == "__main__":
    unittest.main()
