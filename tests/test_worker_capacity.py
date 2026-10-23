"""Tests for WorkerCapacity (atomic persisted concurrency ceiling: 1 or 2)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from bridge.services.worker_capacity import WorkerCapacity


class WorkerCapacityTests(unittest.TestCase):
    def test_default_is_one_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-capacity"
            cap = WorkerCapacity(path)
            self.assertEqual(cap.get(), 1)

    def test_set_and_get_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-capacity"
            cap = WorkerCapacity(path)
            self.assertEqual(cap.set(2), 2)
            self.assertEqual(cap.get(), 2)

    def test_set_and_get_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-capacity"
            cap = WorkerCapacity(path)
            cap.set(2)
            self.assertEqual(cap.set(1), 1)
            self.assertEqual(cap.get(), 1)

    def test_garbage_file_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-capacity"
            path.write_text("garbage\n", encoding="ascii")
            cap = WorkerCapacity(path)
            self.assertEqual(cap.get(), 1)

    def test_file_with_one_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-capacity"
            path.write_text("1\n", encoding="ascii")
            cap = WorkerCapacity(path)
            self.assertEqual(cap.get(), 1)

    def test_file_with_two_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-capacity"
            path.write_text("2\n", encoding="ascii")
            cap = WorkerCapacity(path)
            self.assertEqual(cap.get(), 2)

    def test_set_rejects_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-capacity"
            cap = WorkerCapacity(path)
            with self.assertRaises(ValueError):
                cap.set(0)
            with self.assertRaises(ValueError):
                cap.set(3)
            with self.assertRaises(ValueError):
                cap.set("2")  # type: ignore[arg-type]

    def test_atomic_replace_leaves_no_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-capacity"
            cap = WorkerCapacity(path)
            cap.set(2)
            cap.set(1)
            remaining = [p for p in Path(tmp).iterdir() if p.name.endswith(".tmp")]
            self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
