"""Release activation must never follow attacker-controlled filesystem links."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from bridge.infrastructure.deployment.atomic import AtomicDeployment, ReleaseArtifact


class AtomicReleasePathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.deploy_root = self.root / "deploy"
        self.deploy_root.mkdir()
        self.deployer = AtomicDeployment(self.deploy_root)
        self.package = self.root / "package.bin"
        self.package.write_bytes(b"verified-build")

    async def _deploy(self):
        return await self.deployer.deploy(
            ReleaseArtifact(
                "3.3.0", self.package,
                hashlib.sha256(self.package.read_bytes()).hexdigest(),
            ),
            test=AsyncMock(), migrate=AsyncMock(), health=AsyncMock(),
            restart=AsyncMock(), smoke=AsyncMock(),
        )

    async def test_legitimate_version_directory_is_activated(self):
        target = await self._deploy()
        self.assertEqual(self.deployer.current.resolve(), target)
        self.assertEqual((target / self.package.name).read_bytes(), b"verified-build")

    async def test_preexisting_current_symlink_outside_release_root_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.deployer.current.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "escapes"):
            await self._deploy()
        self.assertFalse((self.deployer.releases / "3.3.0").exists())

    async def test_symlinked_release_store_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.deployer.releases.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            await self._deploy()
        self.assertEqual(list(outside.iterdir()), [])

    async def test_non_symlink_current_and_dangling_current_are_rejected(self):
        self.deployer.current.write_text("not a managed release")
        with self.assertRaisesRegex(ValueError, "must be a symlink"):
            await self._deploy()
        self.deployer.current.unlink()
        self.deployer.current.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "cannot be resolved"):
            await self._deploy()

    async def test_symlink_artifact_is_rejected_before_processing(self):
        destination = self.root / "source-link"
        destination.symlink_to(self.package)
        with self.assertRaisesRegex(ValueError, "ordinary file"):
            await self.deployer.deploy(
                ReleaseArtifact(
                    "3.3.0", destination,
                    hashlib.sha256(self.package.read_bytes()).hexdigest(),
                ),
                test=AsyncMock(), migrate=AsyncMock(), health=AsyncMock(),
                restart=AsyncMock(), smoke=AsyncMock(),
            )
        self.assertFalse(self.deployer.releases.exists())


if __name__ == "__main__":
    unittest.main()
