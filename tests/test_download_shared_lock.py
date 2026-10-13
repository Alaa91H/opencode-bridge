"""Cross-process managed download quota lock and crash recovery tests."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bridge.services.download_service import DownloadBusy, DownloadError, DownloadService

PROJECT = Path(__file__).resolve().parents[1]


class SharedLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "downloads"

    def test_two_independent_instances_cannot_mutate_same_cache(self) -> None:
        first = DownloadService(self.root)
        second = DownloadService(self.root)
        with first._storage_guard():
            with self.assertRaises(DownloadBusy):
                second.cleanup_expired()
            with self.assertRaises(DownloadBusy):
                second.delete("1", "a" * 32)
        self.assertEqual(second.cleanup_expired()["removed"], 0)

    def test_lock_releases_on_regular_python_exception(self) -> None:
        first = DownloadService(self.root)
        with self.assertRaisesRegex(ValueError, "interrupted"):
            with first._storage_guard():
                raise ValueError("interrupted")
        with DownloadService(self.root)._storage_guard():
            pass

    def test_child_process_crash_releases_os_lock(self) -> None:
        service = DownloadService(self.root)
        script = (
            "import os,sys\n"
            "from pathlib import Path\n"
            "from bridge.services.download_service import DownloadService\n"
            "service = DownloadService(Path(sys.argv[1]))\n"
            "fd = service._acquire_file_lock()\n"
            "print('LOCKED', flush=True)\n"
            "sys.stdin.read()\n"
            "os._exit(0)\n"
        )
        with subprocess.Popen(
            [sys.executable, "-c", script, str(self.root)],
            cwd=PROJECT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        ) as child:
            self.assertIsNotNone(child.stdout)
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "LOCKED")
            with self.assertRaises(DownloadBusy):
                service._acquire_file_lock()
            self.assertIsNotNone(child.stdin)
            assert child.stdin is not None
            child.stdin.close()
            self.assertEqual(child.wait(timeout=10), 0)
        with service._storage_guard():
            pass

    def test_symlink_lock_does_not_modify_external_target(self) -> None:
        service = DownloadService(self.root)
        service.ensure_directories()
        target = Path(self.temp.name) / "outside"
        target.write_text("untouched", encoding="utf-8")
        try:
            (self.root / ".downloads.lock").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Host lacks symlink capability")
        with self.assertRaises(DownloadError):
            service.cleanup_expired()
        self.assertEqual(target.read_text(), "untouched")


class AsyncSharedLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_waits_for_held_lock_without_blocking_loop(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = DownloadService(Path(folder) / "downloads")
            second = DownloadService(Path(folder) / "downloads")
            result = object()
            with patch.object(second, "_fetch_locked",
                              new=AsyncMock(return_value=result)):
                with first._storage_guard():
                    task = asyncio.create_task(
                        second.fetch("owner", "https://example.org/test"),
                    )
                    await asyncio.sleep(0.2)
                    self.assertFalse(task.done())
                self.assertIs(await asyncio.wait_for(task, 5), result)


if __name__ == "__main__":
    unittest.main()
