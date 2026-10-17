"""Regression tests for audit-log rotation."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from audit_log import AuditLogger


class AuditRotationTests(unittest.TestCase):
    def test_default_cap_is_ten_mib(self) -> None:
        with TemporaryDirectory() as tmp:
            logger = AuditLogger(Path(tmp) / "audit.jsonl")
            self.assertEqual(logger.max_bytes, 10 * 1024 * 1024)
            self.assertEqual(AuditLogger.MAX_BYTES, 10 * 1024 * 1024)

    def test_oversized_file_rotates_to_single_generation(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path, max_bytes=1024)
            path.write_bytes(b"x" * 1024)
            logger.write("probe", "ok")
            rotated = path.with_name(path.name + ".1")
            self.assertTrue(rotated.is_file())
            self.assertGreater(rotated.stat().st_size, 0)
            # The fresh active file holds only the new entry.
            self.assertEqual(path.read_bytes().count(b"\n"), 1)

    def test_small_file_never_rotates(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path)
            logger.write("probe", "ok")
            logger.write("probe", "ok")
            self.assertFalse(path.with_name(path.name + ".1").exists())
            self.assertEqual(path.read_bytes().count(b"\n"), 2)


if __name__ == "__main__":
    unittest.main()
