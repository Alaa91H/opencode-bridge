"""Secure runtime inventory must reveal state, never secret values or commands."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.services.host_inventory import collect_inventory

SHA = "a" * 40
BAD_SECRET = "ghp_test_secret_never_echo_in_inventory"


class HostInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "runtime").mkdir()
        (self.root / "runtime" / "private").mkdir()
        (self.root / ".env").write_text("GITHUB_TOKEN=" + BAD_SECRET, encoding="ascii")
        (self.root / "runtime" / "private" / "provider-keys.json").write_text(
            '{"token":"' + BAD_SECRET + '"}', encoding="utf-8",
        )
        (self.root / "runtime" / "autopilot-mode").write_text("off\n", encoding="ascii")
        (self.root / "runtime" / "host-auto-update-enabled").write_text(
            "0\n", encoding="ascii",
        )
        (self.root / "runtime" / "deployment-identity").write_text(
            json.dumps({"tag": "v3.2.0", "commit": SHA,
                        "extra": BAD_SECRET}), encoding="ascii",
        )

    def test_secret_safe_allowlisted_commands_and_durable_identity(self) -> None:
        calls: list[tuple[str, ...]] = []

        def command(argv, *, directory):
            self.assertEqual(directory, self.root.resolve())
            calls.append(tuple(argv))
            if argv[:2] == ["git", "rev-parse"]:
                return 0, SHA
            if argv[:3] == ["git", "show", "HEAD:VERSION"]:
                return 0, "3.2.0"
            if argv[:2] == ["git", "diff"]:
                return 0, ""
            if "is-active" in argv:
                return 0, "active"
            self.fail(f"unexpected command type: {argv[0]}")

        with patch("bridge.services.host_inventory._run", side_effect=command):
            data = collect_inventory(self.root)
        dumped = json.dumps(data)
        self.assertNotIn(BAD_SECRET, dumped)
        self.assertNotIn("GITHUB_TOKEN=", dumped)
        self.assertNotIn("provider-keys", dumped)
        self.assertNotIn(str(self.root), dumped)
        self.assertEqual(data["git"]["head_commit"], SHA)
        self.assertEqual(data["deployed"]["commit"], SHA)
        self.assertEqual(data["services"]["opencode-serve.service"], "active")
        self.assertEqual(data["update_policy"]["bridge_mode"], "off")
        self.assertEqual(data["update_policy"]["host_os"], "off")
        self.assertEqual(len(calls), 7)
        self.assertTrue(all(argv[0] in {"git", "systemctl"} for argv in calls))
        self.assertTrue(all("is-active" in argv for argv in calls if argv[0] == "systemctl"))

    def test_missing_host_policy_is_not_reported_as_enabled(self) -> None:
        (self.root / "runtime" / "host-auto-update-enabled").unlink()
        with patch("bridge.services.host_inventory._run", return_value=(127, "")):
            info = collect_inventory(self.root)
        self.assertEqual(info["update_policy"]["host_os"], "unset")
        self.assertIsNone(info["git"]["head_commit"])
        self.assertIsNone(info["git"]["worktree_dirty"])
        self.assertTrue(all(x == "unavailable" for x in info["services"].values()))

    def test_invalid_policy_and_malformed_identity_never_echo_values(self) -> None:
        bad = self.root / "runtime" / "autopilot-mode"
        bad.write_text(BAD_SECRET, encoding="ascii")
        (self.root / "runtime" / "deployment-identity").write_text(
            json.dumps({"tag": BAD_SECRET, "commit": SHA}), encoding="utf-8",
        )
        with patch("bridge.services.host_inventory._run", return_value=(127, "")):
            info = collect_inventory(self.root)
        self.assertEqual(info["update_policy"]["bridge_mode"], "invalid")
        self.assertEqual(info["deployed"], {"status": "invalid"})
        self.assertNotIn(BAD_SECRET, json.dumps(info))

    def test_symlinked_policy_is_refused_even_if_target_is_enabled(self) -> None:
        flag = self.root / "runtime" / "host-auto-update-enabled"
        flag.unlink()
        target = self.root / "enabled"
        target.write_text("1\n", encoding="ascii")
        try:
            flag.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on test host")
        with patch("bridge.services.host_inventory._run", return_value=(127, "")):
            self.assertEqual(collect_inventory(self.root)["update_policy"]["host_os"], "invalid")

    def test_env_content_is_never_read_and_permissions_are_reported(self) -> None:
        if os.name == "posix":
            (self.root / ".env").chmod(0o644)
        with patch("bridge.services.host_inventory._run", return_value=(127, "")):
            info = collect_inventory(self.root)
        self.assertTrue(info["storage"]["env"]["present"])
        if os.name == "posix":
            self.assertFalse(info["storage"]["env"]["safe_permissions"])


if __name__ == "__main__":
    unittest.main()
