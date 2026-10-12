"""Adversarial regressions for transport, ZIPs, deployments and credentials."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from bridge.infrastructure.deployment.atomic import AtomicDeployment, ReleaseArtifact
from bridge.infrastructure.media.archives import UnsafeArchive, extract_zip, zip_manifest
from bridge.infrastructure.sandbox.runner import BuildSandbox, SandboxBackend, SandboxLimits
from bridge.infrastructure.workspaces.manager import Workspace, WorkspaceManager, WorkspacePolicy
from bridge.infrastructure.security.secrets import SystemdCredentialSource
from bridge.services.download_service import DownloadError, DownloadService, DownloadTooLarge, validate_url


class DownloadSecurityRegressions(unittest.IsolatedAsyncioTestCase):
    def test_rejects_embedded_credentials_control_characters_and_bad_ports(self) -> None:
        for url in (
            "http://user:password@public.example/file",
            "https://public.example:99999/file",
            "https://public.example:0/file",
            "https://public.example/\nHeader: injection",
            "http://[not-ipv6]/file",
        ):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                validate_url(url)

    async def test_private_redirect_is_not_requested_even_once(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:9000/secret"})

        real_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as temp:
            service = DownloadService(Path(temp))
            with patch("bridge.services.download_service._assert_resolved_public", new=AsyncMock()), \
                 patch("bridge.services.download_service.httpx.AsyncClient",
                       side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)):
                with self.assertRaises(DownloadError):
                    await service.fetch("user", "https://public.example/download")
        self.assertEqual(seen, ["https://public.example/download"])

    async def test_redirect_cycle_is_bounded_before_seventh_request(self) -> None:
        requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(302, headers={"Location": "/loop"})

        real_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as temp:
            service = DownloadService(Path(temp), allow_private_hosts=True)
            with patch("bridge.services.download_service.httpx.AsyncClient",
                       side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)):
                with self.assertRaises(DownloadError):
                    await service.fetch("42", "http://localhost/loop")
        self.assertEqual(requests, 6)

    async def test_concurrent_transfers_do_not_overcommit_quota_or_index(self) -> None:
        responses = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal responses
            responses += 1
            return httpx.Response(200, content=b"abcdef", headers={"Content-Length": "6"})

        real_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as temp:
            service = DownloadService(Path(temp), max_file_bytes=8, max_total_bytes=10,
                                      allow_private_hosts=True)
            with patch("bridge.services.download_service.httpx.AsyncClient",
                       side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)):
                results = await asyncio.gather(
                    service.fetch("42", "http://localhost/a"),
                    service.fetch("42", "http://localhost/b"), return_exceptions=True)
            successes = [x for x in results if not isinstance(x, BaseException)]
            failures = [x for x in results if isinstance(x, BaseException)]
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], DownloadTooLarge)
            self.assertEqual(service.used_bytes(), 6)
            self.assertEqual(len(service.list("42")), 1)
            self.assertEqual(responses, 2)

    async def test_download_index_write_failure_removes_orphan(self) -> None:
        real_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as temp:
            service = DownloadService(Path(temp), allow_private_hosts=True)
            with patch("bridge.services.download_service.httpx.AsyncClient",
                       side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(
                           lambda request: httpx.Response(200, content=b"x")), **kwargs)), \
                 patch.object(service, "_write_index", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    await service.fetch("42", "http://localhost/file")
            self.assertEqual([p for p in service.root.rglob("*") if p.is_file()], [])


class ArchiveSecurityRegressions(unittest.TestCase):
    def test_duplicate_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "dup.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr("file.txt", "first")
                    zf.writestr("file.txt", "last")
            with self.assertRaises(UnsafeArchive):
                zip_manifest(archive)

    def test_unix_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "symlink.zip"
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(link, "../../etc/passwd")
            with self.assertRaises(UnsafeArchive):
                zip_manifest(archive)

    def test_existing_file_is_never_overwritten_and_partial_output_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "out"
            destination.mkdir()
            (destination / "keep.txt").write_text("original")
            archive = root / "overwrite.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("new.txt", "new")
                zf.writestr("keep.txt", "malicious")
            with self.assertRaises(FileExistsError):
                extract_zip(archive, destination)
            self.assertEqual((destination / "keep.txt").read_text(), "original")
            self.assertFalse((destination / "new.txt").exists())


class DeploymentAndSecretsSecurityRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_release_directory_traversal_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "release.tar.gz"
            package.write_bytes(b"release")
            deployer = AtomicDeployment(root / "deploy")
            for version in ("../outside", "..", "/tmp/outside", "nested/release", "a\\b"):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    await deployer.deploy(
                        ReleaseArtifact(version, package, hashlib.sha256(b"release").hexdigest()),
                        test=AsyncMock(), migrate=AsyncMock(), health=AsyncMock(),
                        restart=AsyncMock(), smoke=AsyncMock())
            self.assertFalse((root / "outside").exists())

    async def test_failed_preflight_does_not_leave_half_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "release.bin"
            package.write_bytes(b"release")
            deployer = AtomicDeployment(root / "deploy")
            with self.assertRaises(RuntimeError):
                await deployer.deploy(
                    ReleaseArtifact("2.1.0", package, hashlib.sha256(b"release").hexdigest()),
                    test=AsyncMock(side_effect=RuntimeError("bad test")), migrate=AsyncMock(),
                    health=AsyncMock(), restart=AsyncMock(), smoke=AsyncMock())
            self.assertFalse((deployer.releases / "2.1.0").exists())

    def test_credential_source_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = SystemdCredentialSource(Path(temp))
            for value in ("../secret", "a/b", "..", "", "a\\b"):
                with self.subTest(name=value), self.assertRaises(ValueError):
                    source.read(value)


class WorkspaceAndSandboxRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_identifier_cannot_escape_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = WorkspaceManager(root / "tasks")
            for value in ("../outside", "nested/task", "..", "/tmp/injected", "a\\b"):
                with self.subTest(task_id=value), self.assertRaises(ValueError):
                    await manager.acquire(value, root)
            self.assertFalse((root / "outside").exists())

    async def test_workspace_release_never_deletes_an_outside_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside"
            outside.mkdir()
            (outside / "keep").write_text("safe")
            manager = WorkspaceManager(root / "tasks", WorkspacePolicy(strategy="clone"))
            fake = Workspace("forged", outside, root, "clone")
            with self.assertRaises(ValueError):
                await manager.release(fake)
            self.assertEqual((outside / "keep").read_text(), "safe")

    async def test_sandbox_process_output_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sandbox = BuildSandbox(
                Path(temp), SandboxLimits(max_output_bytes=256),
                SandboxBackend("podman", "/usr/bin/podman"),
            )
            sandbox.command = lambda argv: [sys.executable, "-c", "print('x' * 10000)"]
            result = await sandbox.run(["test"])
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.stdout.endswith("[output truncated]"))
            self.assertLess(len(result.stdout), 300)


if __name__ == "__main__":
    unittest.main()
