import unittest
from pathlib import Path

from bridge.infrastructure.sandbox.runner import (
    BuildSandbox,
    SandboxBackend,
    SandboxLimits,
    SandboxUnavailable,
)


class BuildSandboxTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path("/tmp/workspace")

    def test_podman_has_resource_and_network_limits(self):
        sandbox = BuildSandbox(self.workspace, SandboxLimits(cpus=1.5, memory_mb=512, disk_mb=1024, processes=32, network=False),
                               SandboxBackend("podman", "/usr/bin/podman"))
        cmd = sandbox.command(["python", "-m", "pytest"])
        joined = " ".join(cmd)
        self.assertIn("--userns=keep-id", joined)
        self.assertIn("--network none", joined)
        self.assertIn("--cpus 1.5", joined)
        self.assertIn("--memory 512m", joined)
        self.assertIn("--pids-limit 32", joined)
        self.assertIn("--read-only", joined)
        self.assertIn("/workspace", joined)
        self.assertNotIn("/var/run/docker.sock", joined)

    def test_bubblewrap_unshares_namespaces_and_network(self):
        sandbox = BuildSandbox(self.workspace, SandboxLimits(network=False), SandboxBackend("bubblewrap", "/usr/bin/bwrap"))
        cmd = sandbox.command(["sh", "-c", "echo ok"])
        for flag in ("--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-net", "--die-with-parent"):
            self.assertIn(flag, cmd)
        self.assertNotIn("/etc", cmd)
        self.assertNotIn("/home", cmd)

    def test_unknown_backend_is_rejected(self):
        sandbox = BuildSandbox(self.workspace, backend=SandboxBackend("host", "/bin/sh"))
        with self.assertRaises(SandboxUnavailable):
            sandbox.command(["echo", "unsafe"])

    def test_empty_command_is_rejected(self):
        sandbox = BuildSandbox(self.workspace, backend=SandboxBackend("podman", "/usr/bin/podman"))
        with self.assertRaises(ValueError):
            sandbox.command([])

    def test_build_profiles_execute_inside_sandbox(self):
        sandbox = BuildSandbox(self.workspace, backend=SandboxBackend("podman", "/usr/bin/podman"))
        self.assertIn("pytest", sandbox.command(["python", "-m", "pytest", "-q"]))
        self.assertIn("npm", sandbox.command(["npm", "test"]))
        self.assertIn("./gradlew", sandbox.command(["./gradlew", "test"]))
