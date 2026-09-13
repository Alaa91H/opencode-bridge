from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workspace_manager import GitWorkspaceManager, WorkspaceError


class WorkspaceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = GitWorkspaceManager(
            self.root,
            ["Alaa91H/opencode-bridge", "Alaa91H/QuranLiveStream"],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_normalize_slug_accepts_common_github_forms(self) -> None:
        self.assertEqual(self.manager.normalize_slug("Alaa91H/opencode-bridge"), "Alaa91H/opencode-bridge")
        self.assertEqual(
            self.manager.normalize_slug("https://github.com/Alaa91H/opencode-bridge.git"),
            "Alaa91H/opencode-bridge",
        )
        self.assertEqual(
            self.manager.normalize_slug("git@github.com:Alaa91H/opencode-bridge.git"),
            "Alaa91H/opencode-bridge",
        )

    def test_rejects_non_github_remote(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.manager.normalize_slug("https://example.com/a/b.git")

    def test_allowlist_is_case_insensitive(self) -> None:
        self.assertEqual(
            self.manager.require_allowed("alaa91h/OPENCODE-BRIDGE"),
            "alaa91h/OPENCODE-BRIDGE",
        )

    def test_rejects_repository_outside_allowlist(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.manager.require_allowed("other/project")

    def test_repo_path_stays_under_workspace_root(self) -> None:
        path = self.manager.repo_path("Alaa91H/opencode-bridge")
        path.relative_to(self.root.resolve())
        self.assertEqual(path.name, "opencode-bridge")
        self.assertEqual(path.parent.name, "Alaa91H")


if __name__ == "__main__":
    unittest.main()
