import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from bridge.infrastructure.deployment.atomic import AtomicDeployment, ReleaseArtifact


class AtomicDeploymentTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_test_migrate_health_switch_restart_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "release.bin"
            package.write_bytes(b"v2")
            deployer = AtomicDeployment(root / "app")
            callbacks = [AsyncMock() for _ in range(5)]
            target = await deployer.deploy(
                ReleaseArtifact("2.0.0", package, hashlib.sha256(b"v2").hexdigest()),
                test=callbacks[0], migrate=callbacks[1], health=callbacks[2],
                restart=callbacks[3], smoke=callbacks[4])
            self.assertEqual(deployer.current.resolve(), target.resolve())
            for callback in callbacks:
                callback.assert_awaited_once()

    async def test_smoke_failure_rolls_back_current_and_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            old = app / "releases" / "1.0.0"
            old.mkdir(parents=True)
            app.mkdir(exist_ok=True)
            (app / "current").symlink_to(old)
            package = root / "release.bin"
            package.write_bytes(b"v2")
            restart = AsyncMock()
            with self.assertRaises(RuntimeError):
                await AtomicDeployment(app).deploy(
                    ReleaseArtifact("2.0.0", package, hashlib.sha256(b"v2").hexdigest()),
                    test=AsyncMock(), migrate=AsyncMock(), health=AsyncMock(),
                    restart=restart, smoke=AsyncMock(side_effect=RuntimeError("bad smoke")))
            self.assertEqual((app / "current").resolve(), old.resolve())
            self.assertEqual(restart.await_count, 2)

    async def test_checksum_failure_stops_before_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "release.bin"
            package.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                await AtomicDeployment(root / "app").deploy(
                    ReleaseArtifact("2", package, "0" * 64),
                    test=AsyncMock(), migrate=AsyncMock(), health=AsyncMock(),
                    restart=AsyncMock(), smoke=AsyncMock())
