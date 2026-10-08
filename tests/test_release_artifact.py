from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_artifact.py"
SPEC = importlib.util.spec_from_file_location("release_artifact", MODULE_PATH)
assert SPEC and SPEC.loader
release_artifact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_artifact)


class ReleaseArtifactTests(unittest.TestCase):
    def test_manifest_binds_checksum_version_and_exact_source_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "bundle.tar.gz"
            artifact.write_bytes(b"immutable github artifact")
            source_sha = "a" * 40
            manifest = release_artifact.create_manifest(artifact, "2.0.0", source_sha)
            self.assertEqual(manifest["source_sha"], source_sha)
            self.assertEqual(manifest["version"], "2.0.0")
            self.assertEqual(manifest["sha256"], release_artifact.sha256_file(artifact))

    def test_verification_rejects_checksum_or_source_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "bundle.tar.gz"
            artifact.write_bytes(b"payload")
            manifest = release_artifact.create_manifest(artifact, "2.0.0", "b" * 40)
            release_artifact.verify_manifest(artifact, manifest, "b" * 40)
            with self.assertRaises(ValueError):
                release_artifact.verify_manifest(artifact, manifest, "c" * 40)
            altered = dict(manifest, sha256="0" * 64)
            with self.assertRaises(ValueError):
                release_artifact.verify_manifest(artifact, altered, "b" * 40)

    def test_manifest_rejects_malformed_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "bundle.tar.gz"
            artifact.write_bytes(b"payload")
            with self.assertRaises(ValueError):
                release_artifact.create_manifest(artifact, "2.0.0", "main")


if __name__ == "__main__":
    unittest.main()
