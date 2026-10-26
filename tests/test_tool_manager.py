"""Regression tests for the owner-controlled tool manager."""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.services.host_control import CommandResult
from bridge.services.tool_manager import (
    SPECS,
    TOOLS,
    ToolManager,
    ToolManagerError,
)

COMMANDS = {
    "git": "git", "gh": "gh", "curl": "curl", "jq": "jq", "rg": "rg",
    "yt-dlp": "yt-dlp", "aria2": "aria2c", "ffmpeg": "ffmpeg",
    "age": "age", "poppler": "pdftotext", "tesseract": "tesseract",
}


class ToolManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.helper = self.root / "opencode-bridge-tools"
        self.helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.helper.chmod(0o700)
        self.installed = {"git", "gh", "curl", "jq"}
        self.present_commands = {COMMANDS[key] for key in self.installed}
        for name in ("ffprobe", "pdfimages", "aria2c"):
            if name in self.present_commands:
                (self.bin_dir / name).write_text("", encoding="utf-8")
        for name in COMMANDS.values():
            if name in self.present_commands:
                (self.bin_dir / name).write_text("", encoding="utf-8")
                (self.bin_dir / name).chmod(0o755)
        self.runner = self._runner
        self.manager = ToolManager(
            self.root, helper=self.helper, runner=self._runner,
            dpkg_query="dpkg-query", dpkg_verify="dpkg",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    async def _runner(self, argv: list[str], *, timeout: float = 0, **_: object) -> CommandResult:
        del timeout
        if argv[0] == "dpkg-query":
            spec = next(spec for spec in TOOLS if spec.package == argv[-1])
            if spec.key in self.installed:
                return CommandResult(0, f"install ok installed\t1.2.3\t{spec.key and 128}\n")
            return CommandResult(1, "")
        if argv[0] == "dpkg":
            return CommandResult(0, "")
        if argv[:2] == ["sudo", "-n"]:
            action, key = argv[-2], argv[-1]
            if action == "install":
                self.installed.add(key)
                self.present_commands.add(COMMANDS[key])
            elif action == "remove":
                self.installed.discard(key)
                self.present_commands.discard(COMMANDS[key])
            return CommandResult(0, "ok")
        raise AssertionError(f"unexpected command: {argv}")

    def _patch_which(self):
        return patch(
            "bridge.services.tool_manager.shutil.which",
            side_effect=lambda command: (
                str(self.bin_dir / command)
                if command in self.present_commands else None
            ),
        )

    async def test_status_reports_package_version_and_capability(self) -> None:
        with self._patch_which():
            status = await self.manager.status_for("jq")
        self.assertTrue(status.installed)
        self.assertTrue(status.package_installed)
        self.assertTrue(status.enabled)
        self.assertEqual(status.version, "1.2.3")
        self.assertEqual(status.size_kib, 128)

    async def test_disable_and_enable_are_policy_only(self) -> None:
        with self._patch_which():
            self.manager.set_enabled("jq", False)
            status = await self.manager.status_for("jq")
            self.assertFalse(status.enabled)
            self.assertTrue(status.installed)
            self.assertFalse(self.manager.enabled("jq"))
            self.manager.set_enabled("jq", True)
            self.assertTrue((await self.manager.status_for("jq")).enabled)
        payload = json.loads(self.manager.policy_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["disabled"], [])

    async def test_core_tool_cannot_be_disabled_or_removed(self) -> None:
        with self.assertRaises(ToolManagerError):
            self.manager.set_enabled("git", False)
        with self._patch_which():
            result = await self.manager.remove("git")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "core_tool")

    async def test_install_clears_removed_policy_and_reports_usable(self) -> None:
        self.installed.discard("jq")
        self.present_commands.discard("jq")
        with self._patch_which():
            before = await self.manager.status_for("jq")
            self.assertFalse(before.installed)
            result = await self.manager.install("jq")
            after = await self.manager.status_for("jq")
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "installed")
        self.assertTrue(after.usable)
        payload = json.loads(self.manager.policy_path.read_text(encoding="utf-8"))
        self.assertNotIn("jq", payload["removed"])
        self.assertNotIn("jq", payload["disabled"])

    async def test_remove_records_policy_and_is_not_reinstalled_by_daily_run(self) -> None:
        with self._patch_which():
            result = await self.manager.remove("jq")
            status = await self.manager.status_for("jq")
        self.assertTrue(result.ok)
        self.assertFalse(status.installed)
        payload = json.loads(self.manager.policy_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["removed"], ["jq"])
        self.assertEqual(payload["disabled"], ["jq"])

    async def test_helper_missing_fails_closed(self) -> None:
        self.installed.discard("jq")
        self.present_commands.discard("jq")
        # Delete the helper instead of patching stat for every path: a global
        # stat patch only raised on 3.12/3.13, because those pathlib versions
        # re-raise unexpected OSErrors from exists() while 3.14 does not.
        self.helper.unlink()
        with self._patch_which():
            result = await self.manager.install("jq")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "unavailable")

    def test_helper_with_unreadable_metadata_fails_closed(self) -> None:
        with patch.object(Path, "stat", side_effect=OSError):
            self.assertFalse(self.manager.helper_available())

    def test_unreadable_policy_is_an_error_not_an_empty_policy(self) -> None:
        with patch.object(Path, "exists", side_effect=OSError):
            with self.assertRaises(ToolManagerError):
                self.manager.set_enabled("jq", True)
            # The fail-closed readers must never turn a capability back on.
            self.assertFalse(self.manager.enabled("jq"))
            self.assertEqual(self.manager.disabled_tools(), frozenset(SPECS))

    async def test_corrupt_policy_disables_capabilities_safely(self) -> None:
        self.manager.policy_path.write_text("{bad", encoding="utf-8")
        self.assertFalse(self.manager.enabled("jq"))
        self.assertEqual(self.manager.disabled_tools(), frozenset(SPECS))
        with self._patch_which(), self.assertRaises(ToolManagerError):
            await self.manager.status_for("jq")

    async def test_policy_must_not_be_a_symlink(self) -> None:
        target = self.root / "outside.json"
        target.write_text('{"schema": 1}', encoding="utf-8")
        self.manager.policy_path.symlink_to(target)
        with self.assertRaises(ToolManagerError):
            self.manager.set_enabled("jq", False)

    async def test_harden_accepts_a_consistent_install(self) -> None:
        with self._patch_which():
            audit = await self.manager.harden()
        self.assertTrue(audit.ok, audit.issues)
        self.assertEqual(audit.checked, len(TOOLS))

    async def test_policy_permissions_are_tightened_on_write(self) -> None:
        self.manager.set_enabled("jq", False)
        mode = stat.S_IMODE(self.manager.policy_path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0)


class BackupCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_age_blocks_encrypted_backup_before_encryption(self) -> None:
        from bridge.services.admin_backups import AdminBackups

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "runtime").mkdir()
            (root / "runtime/backup-age-recipient").write_text("age1example\n", encoding="ascii")
            backups = AdminBackups(root, release_root=root / "releases",
                                   tool_enabled=lambda key: key != "age")
            with self.assertRaisesRegex(ValueError, "disabled"):
                await backups.create_encrypted()


if __name__ == "__main__":
    unittest.main()
