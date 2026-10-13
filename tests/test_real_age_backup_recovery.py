"""Real age encryption and isolated SQLite disaster-recovery smoke test.

CI must run this after installing the official age binary. Only synthetic test
data and ephemeral locally generated keys are used. No production key is read.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from bridge.services.admin_backups import AdminBackups
from bridge.services.backup_recovery import verify_encrypted_backup


class RealAgeRecoverySmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ephemeral_age_backup_can_be_decrypted_and_restored(self) -> None:
        if not shutil.which("age") or not shutil.which("age-keygen"):
            if os.environ.get("AGE_RECOVERY_REQUIRED") == "1":
                self.fail("age and age-keygen are required in the recovery CI job")
            self.skipTest("age binaries are not installed on this system")

        with tempfile.TemporaryDirectory(prefix="bridge-age-staging-") as temporary:
            work = Path(temporary)
            project = work / "bridge"
            runtime = project / "runtime"
            runtime.mkdir(parents=True)
            recovery = work / "offline-recovery"
            recovery.mkdir(mode=0o700)
            identity = recovery / "identity.txt"

            keygen = subprocess.run(  # noqa: S603 -- fixed, trusted binary and test-only identity
                ["age-keygen", "-o", str(identity)],
                check=True, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(keygen.returncode, 0)
            if os.name == "posix":
                identity.chmod(0o600)
            recipient = subprocess.run(  # noqa: S603 -- fixed read-only age-keygen args
                ["age-keygen", "-y", str(identity)],
                check=True, capture_output=True, text=True, timeout=20,
            ).stdout.strip()
            self.assertTrue(recipient.startswith("age1"))
            (runtime / "backup-age-recipient").write_text(recipient + "\n", encoding="ascii")
            (runtime / "autopilot-mode").write_text("off\n", encoding="ascii")
            (runtime / "host-auto-update-enabled").write_text("0\n", encoding="ascii")

            db_path = project / "sessions.db"
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE recovery_probe (value TEXT NOT NULL)")
                db.execute("INSERT INTO recovery_probe VALUES (?)", ("resumable-fixture",))
            (project / ".env").write_text("FAKE_CI_VALUE=ephemeral\n", encoding="ascii")

            backup_manager = AdminBackups(project, release_root=work / "releases")
            record = await backup_manager.create_encrypted()
            encrypted = backup_manager.get(record["name"])
            self.assertTrue(record["includes_keys"])
            self.assertTrue(record["encrypted"])
            self.assertTrue(encrypted.is_file())
            self.assertTrue((encrypted.parent / (encrypted.name + ".json")).is_file())

            result = verify_encrypted_backup(encrypted, identity)
            self.assertEqual(result.sqlite_integrity, "ok")
            self.assertGreaterEqual(result.member_count, 4)
            self.assertGreater(result.database_bytes, 0)
            self.assertEqual(result.encrypted_sha256, record["sha256"])
            self.assertNotIn("ephemeral", json.dumps(result.__dict__))

            archive = recovery / "restored.tar.gz"
            subprocess.run(  # noqa: S603 -- fixed trusted age binary and isolated synthetic data
                ["age", "--decrypt", "--identity", str(identity),
                 "--output", str(archive), str(encrypted)],
                check=True, capture_output=True, timeout=45,
            )
            restored_path = recovery / "restored.db"
            with tarfile.open(archive, mode="r:gz") as bundle:
                self.assertIn("sessions.db", bundle.getnames())
                self.assertIn("runtime/autopilot-mode", bundle.getnames())
                self.assertIn("runtime/host-auto-update-enabled", bundle.getnames())
                original = bundle.extractfile("sessions.db")
                self.assertIsNotNone(original)
                assert original is not None
                with original, restored_path.open("xb") as restored:
                    shutil.copyfileobj(original, restored, 1024 * 1024)
            with sqlite3.connect(f"file:{restored_path}?mode=ro", uri=True) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    db.execute("SELECT value FROM recovery_probe").fetchone()[0],
                    "resumable-fixture",
                )

            # Even valid-looking ciphertext must be integrity-bound to the manifest.
            encrypted.write_bytes(encrypted.read_bytes() + b"modified")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_encrypted_backup(encrypted, identity)
            # Secret plaintext exists only inside the isolated CI recovery tempdir.
            self.assertFalse(any(backup_manager.root.rglob("*.tar.gz")))


if __name__ == "__main__":
    unittest.main()
