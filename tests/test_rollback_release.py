"""Black-box contract tests for the emergency rollback entry point.

The real deployment script is replaced by a harmless recorder. These tests must
never access systemd, sudo, the network, or a production checkout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
if not BASH and os.name == "nt":
    candidate = Path("C:/Program Files/Git/bin/bash.exe")
    if candidate.exists():
        BASH = str(candidate)


@unittest.skipUnless(BASH and shutil.which("git"), "git and bash are required")
class RollbackShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="bridge-rollback-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "runtime").mkdir()
        shutil.copy2(ROOT / "scripts" / "rollback.sh", self.root / "scripts" / "rollback.sh")
        script = self.root / "scripts" / "deploy.sh"
        script.write_text(
            '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
            'printf "%s" "$1" > runtime/deployed-tag-attempt\n'
            'printf "%s" "${OPENCODE_EXPECTED_RELEASE_COMMIT:-}" > runtime/expected-commit\n',
            encoding="utf-8", newline="\n",
        )
        script.chmod(0o755)

        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.first = self.commit_version("1.0.0", tag=True)
        self.untagged = self.commit_version("1.0.1", tag=False)
        self.current = self.commit_version("1.1.0", tag=True)
        (self.root / "runtime" / "previous-deployed-ref").write_text(
            self.first + "\n", encoding="ascii",
        )

    def git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=self.root, check=True,
            capture_output=True, text=True,
        )
        return completed.stdout.strip()

    def commit_version(self, version: str, *, tag: bool) -> str:
        (self.root / "VERSION").write_text(version + "\n", encoding="ascii")
        self.git("add", "VERSION")
        self.git("commit", "-q", "-m", version)
        commit = self.git("rev-parse", "HEAD")
        if tag:
            self.git("tag", "v" + version)
        return commit

    def rollback(self, *args: str, expected: str | None = None):
        env = os.environ.copy()
        if expected:
            env["OPENCODE_EXPECTED_RELEASE_COMMIT"] = expected
        return subprocess.run(
            [str(BASH), "scripts/rollback.sh", *args],
            cwd=self.root, env=env, capture_output=True, text=True,
        )

    def attempted(self) -> str | None:
        path = self.root / "runtime" / "deployed-tag-attempt"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def test_recorded_commit_resolves_to_published_tag(self) -> None:
        result = self.rollback(expected="f" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attempted(), "v1.0.0")
        self.assertEqual(
            (self.root / "runtime" / "expected-commit").read_text(), self.first,
        )

    def test_explicit_tag_invokes_same_guarded_deployer(self) -> None:
        result = self.rollback("v1.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attempted(), "v1.0.0")

    def test_untagged_commit_is_not_deployed(self) -> None:
        result = self.rollback(self.untagged)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.attempted())

    def test_mismatched_tag_is_not_deployed(self) -> None:
        self.git("tag", "-f", "v1.0.0", self.untagged)
        result = self.rollback(self.first)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.attempted())

    def test_current_release_is_no_op(self) -> None:
        result = self.rollback(self.current)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.attempted())

    def test_rejects_revision_operators(self) -> None:
        result = self.rollback("HEAD~1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.attempted())

    def test_missing_previous_record_is_safe(self) -> None:
        (self.root / "runtime" / "previous-deployed-ref").unlink()
        result = self.rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.attempted())


if __name__ == "__main__":
    unittest.main()
