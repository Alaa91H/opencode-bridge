import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.database.backup import BackupService
from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase


class BackupRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_online_backup_verify_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = BridgeDatabase(root / "live.db")
            await MigrationRunner(db).migrate()
            conn = await db.connect()
            await conn.execute(
                "INSERT INTO user_settings(owner_id,settings_json,updated_at) VALUES (?,?,?)",
                ("owner", '{"language":"ar"}', "now"))
            await conn.commit()
            service = BackupService(db, rpo_seconds=1800, rto_seconds=300)
            backup = root / "backup.db"
            manifest = await service.create(backup)
            self.assertTrue(service.verify(backup, manifest))
            self.assertEqual(manifest.rpo_seconds, 1800)
            restored = root / "restored.db"
            service.restore(backup, restored)
            import sqlite3
            check = sqlite3.connect(str(restored))
            try:
                row = check.execute(
                    "SELECT settings_json FROM user_settings WHERE owner_id='owner'").fetchone()
                self.assertEqual(row[0], '{"language":"ar"}')
            finally:
                check.close()
            await db.close()

    async def test_optional_encryption_hook_produces_encrypted_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = BridgeDatabase(root / "live.db")
            await MigrationRunner(db).migrate()
            backup = root / "backup.db"
            await BackupService(db, encrypt=lambda data: b"encrypted:" + data[:8]).create(backup)
            self.assertTrue(backup.with_suffix(".db.enc").read_bytes().startswith(b"encrypted:"))
            await db.close()
