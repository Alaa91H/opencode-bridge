"""Offline age-backup validation and hostile archive regression tests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.services.backup_recovery import verify_encrypted_backup


class EncryptedRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / "backup-20261013T010000-deadbeef.age"
        self.backup.write_bytes(b"encrypted-content-test-fixture")
        self.manifest = self.root / (self.backup.name + ".json")
        self.manifest.write_text(json.dumps({
            "encrypted": True, "includes_keys": True,
            "sha256": hashlib.sha256(self.backup.read_bytes()).hexdigest(),
        }), encoding="utf-8")
        self.identity = self.root / "identity.txt"
        self.identity.write_text("TEST-IDENTITY", encoding="ascii")
        if os.name == "posix":
            self.identity.chmod(0o600)
        self.database = self.root / "example.db"
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE jobs(task TEXT)")
            db.execute("INSERT INTO jobs VALUES ('resume-me')")

    def archive(self, *, members: list[tuple[str, bytes]] | None = None,
                add_symlink: bool = False) -> bytes:
        if members is None:
            members = [("sessions.db", self.database.read_bytes()),
                       ("runtime/private/host-credentials.json", b'{"opaque":true}')]
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
            for name, content in members:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                bundle.addfile(info, io.BytesIO(content))
            if add_symlink:
                info = tarfile.TarInfo("sessions.db")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                bundle.addfile(info)
        return stream.getvalue()

    def verify_using_fake_decryption(self, contents: bytes):
        staged = []

        def fake_age(command, **kwargs):
            self.assertEqual(command[1:3], ["--decrypt", "--identity"])
            self.assertTrue(kwargs["check"])
            self.assertTrue(kwargs["capture_output"])
            output = Path(command[command.index("--output") + 1])
            staged.append(output)
            output.write_bytes(contents)
            return subprocess.CompletedProcess(command, 0)

        with patch("bridge.services.backup_recovery.shutil.which", return_value="/usr/bin/age"), \
             patch("bridge.services.backup_recovery.subprocess.run", side_effect=fake_age):
            result = verify_encrypted_backup(self.backup, self.identity)
        self.assertFalse(staged[0].exists(), "Plaintext staging must be removed")
        return result

    def test_read_only_restore_check_verifies_db_and_rejects_secrets_output(self) -> None:
        result = self.verify_using_fake_decryption(self.archive())
        self.assertEqual(result.sqlite_integrity, "ok")
        self.assertEqual(result.member_count, 2)
        self.assertGreater(result.database_bytes, 0)
        self.assertEqual(result.encrypted_sha256,
                         hashlib.sha256(self.backup.read_bytes()).hexdigest())
        self.assertNotIn("opaque", str(result))

    def test_checksum_corruption_is_rejected_before_decryptor_runs(self) -> None:
        self.backup.write_bytes(b"modified")
        with patch("bridge.services.backup_recovery.subprocess.run") as decryptor:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_encrypted_backup(self.backup, self.identity)
        decryptor.assert_not_called()

    def test_reject_path_traversal_and_unknown_members(self) -> None:
        for name in ("../escape", "/root/.ssh/id_rsa", "nested/unexpected"):
            with self.subTest(member=name), self.assertRaises(ValueError):
                self.verify_using_fake_decryption(self.archive(
                    members=[("sessions.db", self.database.read_bytes()),
                             (name, b"exfiltrated-data")]))

    def test_reject_duplicate_member_and_symlink(self) -> None:
        for payload in (
            self.archive(members=[("sessions.db", self.database.read_bytes())] * 2),
            self.archive(add_symlink=True),
        ):
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                self.verify_using_fake_decryption(payload)

    def test_reject_archive_without_valid_sqlite(self) -> None:
        for payload in (
            self.archive(members=[(".env", b"TOKEN=SECRET")]),
            self.archive(members=[("sessions.db", b"not sqlite")]),
        ):
            with self.assertRaises(ValueError):
                self.verify_using_fake_decryption(payload)

    def test_reject_symlink_identity_and_untrusted_metadata(self) -> None:
        self.manifest.write_text('{"encrypted":true,"includes_keys":false}', encoding="utf-8")
        with self.assertRaises(ValueError):
            verify_encrypted_backup(self.backup, self.identity)
        self.manifest.write_text(json.dumps({
            "encrypted": True, "includes_keys": True,
            "sha256": hashlib.sha256(self.backup.read_bytes()).hexdigest(),
        }), encoding="utf-8")
        linked = self.root / "linked-age-key"
        try:
            linked.symlink_to(self.identity)
        except (NotImplementedError, OSError):
            return
        with self.assertRaisesRegex(ValueError, "unsafe recovery file"):
            verify_encrypted_backup(self.backup, linked)

    def test_decryption_error_is_sanitized(self) -> None:
        with patch("bridge.services.backup_recovery.shutil.which", return_value="/usr/bin/age"), \
             patch("bridge.services.backup_recovery.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, ["age"], stderr=b"PASSWORD")):
            with self.assertRaisesRegex(ValueError, "Unable to decrypt") as result:
                verify_encrypted_backup(self.backup, self.identity)
            self.assertNotIn("PASSWORD", str(result.exception))


if __name__ == "__main__":
    unittest.main()
