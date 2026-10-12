"""Crash-safe release state transitions and restart recovery tests."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import tempfile
import unittest
from pathlib import Path

from bridge.services.update_coordinator import UpdateCoordinator


class UpdateCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.coordinator = UpdateCoordinator(self.root / "runtime")
        self.sha = "a" * 40

    def test_full_verified_activation_and_persistent_history(self) -> None:
        self.assertIsNone(self.coordinator.latest())
        self.coordinator.transition("v3.3.0", self.sha, "verified")
        self.coordinator.transition("v3.3.0", self.sha, "applying")
        self.coordinator.transition("v3.3.0", self.sha, "healthy")
        restored = UpdateCoordinator(self.root / "runtime")
        self.assertEqual(restored.latest().phase, "healthy")
        self.assertEqual([row[1] for row in restored.history()],
                         ["healthy", "applying", "verified"])
        with self.assertRaises(ValueError):
            restored.transition("v3.3.0", self.sha, "applying")

    def test_only_one_concurrent_activation_claim_is_accepted(self) -> None:
        self.coordinator.transition("v3.3.0", self.sha, "verified")
        gate = Barrier(2)

        def claim() -> str:
            gate.wait(timeout=5)
            try:
                UpdateCoordinator(self.root / "runtime").transition(
                    "v3.3.0", self.sha, "applying",
                )
            except ValueError:
                return "rejected"
            return "claimed"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertCountEqual(results, ["claimed", "rejected"])
        self.assertEqual(self.coordinator.latest().phase, "applying")
        self.assertEqual(
            [event[1] for event in self.coordinator.history()],
            ["applying", "verified"],
        )

    def test_unfinished_release_blocks_other_release_activation(self) -> None:
        other_commit = "b" * 40
        self.coordinator.transition("v3.3.0", self.sha, "verified")
        self.coordinator.transition("v3.3.0", self.sha, "applying")
        self.coordinator.transition("v3.4.0", other_commit, "verified")
        with self.assertRaisesRegex(ValueError, "needs recovery"):
            self.coordinator.transition("v3.4.0", other_commit, "applying")
        self.coordinator.transition("v3.3.0", self.sha, "quarantined")
        self.coordinator.transition("v3.4.0", other_commit, "applying")
        self.assertEqual(self.coordinator.latest().phase, "applying")

    def test_restart_cannot_claim_same_applying_release(self) -> None:
        self.coordinator.transition("v3.3.0", self.sha, "verified")
        self.coordinator.transition("v3.3.0", self.sha, "applying")
        with self.assertRaisesRegex(ValueError, "already claimed"):
            UpdateCoordinator(self.root / "runtime").transition(
                "v3.3.0", self.sha, "applying",
            )

    def test_interrupted_application_remains_blocked(self) -> None:
        self.coordinator.transition("v3.3.0", self.sha, "verified")
        self.coordinator.transition("v3.3.0", self.sha, "applying")
        restarted = UpdateCoordinator(self.root / "runtime")
        with self.assertRaises(ValueError):
            restarted.transition("v3.3.0", self.sha, "verified")
        restarted.transition("v3.3.0", self.sha, "quarantined")
        self.assertEqual(restarted.latest().phase, "quarantined")
        with self.assertRaises(ValueError):
            restarted.transition("v3.3.0", self.sha, "applying")

    def test_repeated_verified_check_is_idempotent(self) -> None:
        first = self.coordinator.transition("v3.3.0", self.sha, "verified")
        again = self.coordinator.transition("v3.3.0", self.sha, "verified")
        self.assertEqual(first, again)
        self.assertEqual(len(self.coordinator.history()), 1)
        self.coordinator.transition("v3.3.0", self.sha, "available")
        self.coordinator.transition("v3.3.0", self.sha, "verified")
        self.assertEqual(len(self.coordinator.history()), 3)

    def test_tag_and_commit_mismatch_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.coordinator.transition("../bad", self.sha, "verified")
        with self.assertRaises(ValueError):
            self.coordinator.transition("v3.3.0", "b" * 20, "verified")
        with self.assertRaises(ValueError):
            self.coordinator.transition("v3.3.0", self.sha, "healthy")
        self.coordinator.transition("v3.3.0", self.sha, "verified")
        with self.assertRaises(ValueError):
            self.coordinator.transition("v3.3.1", self.sha, "verified")

    def test_sqlite_journal_is_not_shared_task_database(self) -> None:
        self.coordinator.transition("v3.3.0", self.sha, "verified")
        self.assertFalse((self.root / "sessions.db").exists())
        with sqlite3.connect(self.coordinator.path) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaises(ValueError):
            self.coordinator.history(1000)


if __name__ == "__main__":
    unittest.main()
