"""Real filesystem regression tests for credential, backup and policy controls."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bridge.services.admin_backups import AdminBackups
from bridge.services.host_credentials import HostCredentialManager
from bridge.services.runtime_controls import RuntimeControls


class HostCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = HostCredentialManager(self.root)

    async def test_add_activate_replace_and_delete(self):
        await self.manager.save("github", "primary", "validSecret123")
        await self.manager.activate("github", "primary")
        self.assertIn("GITHUB_TOKEN=validSecret123", (self.root / ".env").read_text())
        self.assertNotIn("validSecret123", str(self.manager.catalog()))
        with self.assertRaises(ValueError):
            await self.manager.delete("github", "primary")
        await self.manager.save("github", "secondary", "newSecret4567")
        await self.manager.activate("github", "secondary")
        await self.manager.delete("github", "primary")
        self.assertEqual(len([x for x in self.manager.catalog() if x["scope"] == "github"]), 1)
        await self.manager.save("github", "secondary", "anotherSecret", replace=True)
        self.assertIn("anotherSecret", (self.root / ".env").read_text())
        if os.name == "posix":
            self.assertEqual((self.root / ".env").stat().st_mode & 0o777, 0o600)

    async def test_reject_symlinks_and_bad_values(self):
        with self.assertRaises(ValueError):
            await self.manager.save("github", "../../outside", "1234567890")
        with self.assertRaises(ValueError):
            await self.manager.save("github", "test", "invalid\nTOKEN")
        (self.root / ".env").symlink_to(self.root / "other.env")
        await self.manager.save("telegram", "first", "111111111111")
        with self.assertRaises(ValueError):
            await self.manager.activate("telegram", "first")


class AdminBackupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        with sqlite3.connect(self.root / "sessions.db") as db:
            db.execute("CREATE TABLE messages (body TEXT)")
            db.execute("INSERT INTO messages VALUES ('hello')")
        self.backups = AdminBackups(self.root)

    async def test_create_verify_sendable_and_delete(self):
        item = await self.backups.create()
        self.assertFalse(item["includes_keys"])
        self.assertEqual(len(self.backups.list()), 1)
        with sqlite3.connect(self.backups.get(item["name"])) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT body FROM messages").fetchone()[0], "hello")
        self.backups.delete(item["name"])
        self.assertFalse(self.backups.list())

    async def test_encrypted_metadata_is_discoverable_and_cleanup_removes_metadata(self):
        self.backups.root.mkdir(parents=True, exist_ok=True)
        names = []
        for second in ("00", "01", "02"):
            name = f"backup-20261012T2350{second}-deadbeef.age"
            path = self.backups.root / name
            payload = ("encrypted-test-" + second).encode()
            path.write_bytes(payload)
            metadata = {"created_utc": "2026-10-12T23:50:00+00:00",
                        "includes_keys": True, "encrypted": True,
                        "sha256": hashlib.sha256(payload).hexdigest()}
            path.with_name(path.name + ".json").write_text(json.dumps(metadata))
            names.append(name)
        self.assertEqual(len(self.backups.list()), 3)
        self.assertEqual(self.backups.purge_after_verified_update(), 1)
        self.assertEqual(len(self.backups.list()), 2)
        self.assertFalse((self.backups.root / names[0]).exists())
        self.assertFalse((self.backups.root / (names[0] + ".json")).exists())

    async def test_invalid_database_backup_does_not_count_as_recovery_copy(self):
        names = [(await self.backups.create())["name"] for _ in range(3)]
        corrupt = self.backups.get(names[-1])
        corrupt.write_bytes(b"not a valid SQLite database")
        self.assertEqual(self.backups.purge_after_verified_update(), 0)
        self.assertEqual(len(self.backups.list()), 3)

    async def test_plain_snapshot_has_content_hash_without_in_memory_read(self):
        from unittest.mock import patch

        # Snapshot should be streamed instead of read entirely into memory.
        with patch.object(Path, "read_bytes", side_effect=AssertionError("full read forbidden")):
            created = await self.backups.create()
        records = self.backups.list()
        self.assertEqual(records[0]["name"], created["name"])
        self.assertEqual(len(created["sha256"]), 64)
        self.assertTrue(self.backups._backup_usable(records[0]))

    async def test_valid_but_modified_sqlite_does_not_authorize_backup_pruning(self):
        names = [(await self.backups.create())["name"] for _ in range(3)]
        newest = self.backups.get(names[-1])
        with sqlite3.connect(newest) as db:
            db.execute("UPDATE messages SET body = 'silently modified'")
        with sqlite3.connect(newest) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        listed = self.backups.list()
        tampered = next(item for item in listed if item["name"] == names[-1])
        self.assertFalse(self.backups._backup_usable(tampered))
        self.assertEqual(self.backups.purge_after_verified_update(), 0)
        self.assertEqual(len(self.backups.list()), 3)

    async def test_legacy_hashless_backup_remains_visible_but_never_pruned(self):
        created = await self.backups.create()
        meta = self.backups.get(created["name"]).with_suffix(".json")
        payload = json.loads(meta.read_text(encoding="utf-8"))
        payload.pop("sha256")
        meta.write_text(json.dumps(payload), encoding="utf-8")
        legacy = self.backups.list()[0]
        self.assertFalse(self.backups._backup_usable(legacy))
        self.assertEqual(self.backups.purge_after_verified_update(), 0)
        self.assertEqual(len(self.backups.list()), 1)

    async def test_export_with_keys_requires_age_recipient(self):
        with self.assertRaises(ValueError):
            await self.backups.create_encrypted()

    async def test_auto_purge_preserves_two_recovery_candidates(self):
        names = [(await self.backups.create())["name"] for _ in range(5)]
        self.assertEqual(len(self.backups.list()), 5)
        self.assertEqual(self.backups.purge_after_verified_update(), 3)
        survivors = self.backups.list()
        self.assertEqual(len(survivors), 2)
        self.assertTrue(all(item["name"] in names for item in survivors))
        for item in survivors:
            with sqlite3.connect(self.backups.get(item["name"])) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.backups.purge_after_verified_update(), 0)



class RuntimePolicyTests(unittest.TestCase):
    def test_os_update_and_purge_are_independently_controlled(self):
        with tempfile.TemporaryDirectory() as folder:
            controls = RuntimeControls(Path(folder))
            self.assertFalse(controls.system_auto_update())  # Privileged changes are opt-in
            self.assertFalse(controls.purge_backups_after_update())
            controls.set_system_auto_update(True)
            self.assertTrue(RuntimeControls(Path(folder)).system_auto_update())
            controls.set_system_auto_update(False)
            controls.set_purge_backups_after_update(True)
            again = RuntimeControls(Path(folder))
            self.assertFalse(again.system_auto_update())
            self.assertTrue(again.purge_backups_after_update())

    def test_bad_host_update_flag_cannot_enable_root_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "host-auto-update-enabled"
            state.write_text("probably\n", encoding="ascii")
            self.assertFalse(RuntimeControls(root).system_auto_update())
            state.unlink()
            try:
                state.symlink_to(root / "elsewhere")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unsupported on this host")
            with self.assertRaises(ValueError):
                RuntimeControls(root).system_auto_update()
