import hashlib
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.database.migration_safety import (
    MigrationPlan, MigrationSafety, Snapshot,
)


class MigrationSafetyTests(unittest.TestCase):
    def test_version_checksum_and_compatibility_window(self):
        plan = MigrationPlan(12, "ALTER TABLE x ADD COLUMN y TEXT", 10)
        self.assertEqual(len(plan.checksum), 64)
        self.assertTrue(plan.compatible_with(10))
        self.assertTrue(plan.compatible_with(12))
        self.assertFalse(plan.compatible_with(9))
        MigrationSafety().validate_checksum(plan, plan.checksum)
        with self.assertRaises(RuntimeError):
            MigrationSafety().validate_checksum(plan, "bad")

    def test_dangerous_self_update_requires_verified_snapshot(self):
        plan = MigrationPlan(12, "DROP TABLE old", 11, reversible=False)
        safety = MigrationSafety()
        with self.assertRaises(RuntimeError):
            safety.require_snapshot(plan, None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.db"
            path.write_bytes(b"db snapshot")
            snapshot = Snapshot(path, hashlib.sha256(b"db snapshot").hexdigest())
            safety.require_snapshot(plan, snapshot)
            path.write_bytes(b"tampered")
            with self.assertRaises(RuntimeError):
                safety.require_snapshot(plan, snapshot)

    def test_rollback_strategy(self):
        safety = MigrationSafety()
        self.assertEqual(safety.rollback_strategy(MigrationPlan(2, "x", 1, True)), "reverse_migration")
        self.assertEqual(safety.rollback_strategy(MigrationPlan(2, "x", 1, False)), "restore_snapshot")
