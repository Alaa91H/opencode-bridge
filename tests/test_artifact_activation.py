from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import tarfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.release_artifact import create_manifest
from maintenance.artifact_activation import ArtifactActivator


class ArtifactActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release_root = self.root / "releases"
        self.current = self.root / "current"
        self.legacy = self.root / "legacy"
        self.legacy.mkdir()
        (self.legacy / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        self.shared_runtime = self.root / "runtime"
        self.shared_runtime.mkdir()
        self.shared_database = self.root / "sessions.db"
        self.shared_database.write_bytes(b"existing database")
        self.archive = self.root / "release.tar.gz"
        self.manifest_path = self.root / "manifest.json"
        self.source_sha = "a" * 40
        self._write_archive({"VERSION": b"2.0.0\n", "run_v3.py": b"print('ready')\n"})
        manifest = create_manifest(self.archive, "2.0.0", self.source_sha)
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.activator = ArtifactActivator(
            self.release_root, self.current, self.legacy,
            self.shared_runtime, self.shared_database,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_archive(self, files: dict[str, bytes]) -> None:
        with tarfile.open(self.archive, "w:gz") as bundle:
            for name, contents in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(contents)
                info.mode = 0o644
                bundle.addfile(info, io.BytesIO(contents))

    def test_activation_switches_only_after_preparation_and_preserves_shared_state(self) -> None:
        prepare = Mock()
        restart = Mock()
        smoke = Mock()
        release = self.activator.activate(
            self.archive, self.manifest_path, self.source_sha, "2.0.0",
            prepare=prepare, restart=restart, smoke=smoke,
        )
        self.assertEqual(self.current.resolve(), release.resolve())
        self.assertEqual((release / "runtime").resolve(), self.shared_runtime.resolve())
        self.assertEqual((release / "sessions.db").resolve(), self.shared_database.resolve())
        prepare.assert_called_once_with(release)
        restart.assert_called_once_with()
        smoke.assert_called_once_with(release)

    def test_failed_smoke_restores_previous_release_and_restarts_it(self) -> None:
        restart = Mock()
        with self.assertRaisesRegex(RuntimeError, "smoke failed"):
            self.activator.activate(
                self.archive, self.manifest_path, self.source_sha, "2.0.0",
                prepare=Mock(), restart=restart,
                smoke=Mock(side_effect=RuntimeError("smoke failed")),
            )
        self.assertEqual(self.current.resolve(), self.legacy.resolve())
        self.assertEqual(restart.call_count, 2)

    def test_source_sha_and_checksum_mismatch_do_not_switch_release(self) -> None:
        for expected_sha in ("b" * 40,):
            with self.subTest(expected_sha=expected_sha), self.assertRaises(ValueError):
                self.activator.activate(
                    self.archive, self.manifest_path, expected_sha, "2.0.0",
                    prepare=Mock(), restart=Mock(), smoke=Mock(),
                )
            self.assertFalse(self.current.exists())

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["sha256"] = hashlib.sha256(b"wrong").hexdigest()
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.activator.activate(
                self.archive, self.manifest_path, self.source_sha, "2.0.0",
                prepare=Mock(), restart=Mock(), smoke=Mock(),
            )
        self.assertFalse(self.current.exists())

    def test_path_traversal_archive_is_rejected_before_extraction(self) -> None:
        self._write_archive({"../escape.txt": b"unsafe"})
        manifest = create_manifest(self.archive, "2.0.0", self.source_sha)
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.activator.activate(
                self.archive, self.manifest_path, self.source_sha, "2.0.0",
                prepare=Mock(), restart=Mock(), smoke=Mock(),
            )
        self.assertFalse((self.root / "escape.txt").exists())
        self.assertFalse(self.current.exists())


if __name__ == "__main__":
    unittest.main()
