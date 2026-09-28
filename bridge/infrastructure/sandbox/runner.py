"""Rootless build sandbox command planner and runner.

The host never installs task dependencies. Commands execute through an available
isolation backend, preferring rootless Podman, then bubblewrap.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path


class SandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxLimits:
    cpus: float = 2.0
    memory_mb: int = 2048
    disk_mb: int = 4096
    wall_seconds: int = 900
    processes: int = 256
    network: bool = False


@dataclass(frozen=True)
class SandboxBackend:
    name: str
    executable: str


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str


def detect_backend() -> SandboxBackend:
    podman = shutil.which("podman")
    if podman:
        return SandboxBackend("podman", podman)
    bwrap = shutil.which("bwrap")
    if bwrap:
        return SandboxBackend("bubblewrap", bwrap)
    raise SandboxUnavailable("no supported rootless sandbox backend found")


class BuildSandbox:
    def __init__(self, workspace: Path, limits: SandboxLimits = SandboxLimits(),
                 backend: SandboxBackend | None = None) -> None:
        self.workspace = workspace.resolve()
        self.limits = limits
        self.backend = backend or detect_backend()

    def command(self, argv: list[str]) -> list[str]:
        if not argv:
            raise ValueError("empty sandbox command")
        if self.backend.name == "podman":
            network = "slirp4netns" if self.limits.network else "none"
            return [
                self.backend.executable, "run", "--rm", "--userns=keep-id",
                "--network", network, "--cpus", str(self.limits.cpus),
                "--memory", f"{self.limits.memory_mb}m",
                "--pids-limit", str(self.limits.processes),
                "--read-only", "--tmpfs", f"/tmp:rw,size={self.limits.disk_mb}m",
                "-v", f"{self.workspace}:/workspace:rw,Z", "-w", "/workspace",
                "docker.io/library/python:3.12-slim", *argv,
            ]
        if self.backend.name == "bubblewrap":
            args = [
                self.backend.executable, "--die-with-parent", "--new-session",
                "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
                "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
                "--bind", str(self.workspace), "/workspace", "--chdir", "/workspace",
                "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
            ]
            if not self.limits.network:
                args.append("--unshare-net")
            return [*args, "--", *argv]
        raise SandboxUnavailable(f"unsupported backend: {self.backend.name}")

    async def run(self, argv: list[str]) -> SandboxResult:
        process = await asyncio.create_subprocess_exec(
            *self.command(argv), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.limits.wall_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return SandboxResult(process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace"))

    async def python_tests(self) -> SandboxResult:
        return await self.run(["python", "-m", "pytest", "-q"])

    async def npm_tests(self) -> SandboxResult:
        return await self.run(["npm", "test"])

    async def gradle_tests(self) -> SandboxResult:
        return await self.run(["./gradlew", "test"])
