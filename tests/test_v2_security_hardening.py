import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.security.hardening import (
    SYSTEMD_HARDENING,
    inspect_magic,
    require_loopback_opencode,
    safe_output_path,
    subprocess_timeout,
)


class SecurityHardeningTests(unittest.TestCase):
    def test_opencode_is_loopback_only(self):
        for url in ("http://localhost:4096", "http://127.0.0.1:4096", "http://[::1]:4096"):
            require_loopback_opencode(url)
        with self.assertRaises(ValueError):
            require_loopback_opencode("http://192.168.1.5:4096")

    def test_output_jail_blocks_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "out"
            root.mkdir()
            self.assertEqual(safe_output_path(root, "task/result.txt"), root / "task/result.txt")
            with self.assertRaises(ValueError):
                safe_output_path(root, "../secret")
            outside = Path(tmp) / "outside"
            outside.write_text("x")
            link = root / "link"
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                safe_output_path(root, "link")

    def test_file_magic(self):
        self.assertEqual(inspect_magic(b"%PDF-1.7"), "application/pdf")
        self.assertEqual(inspect_magic(b"PK\x03\x04rest"), "application/zip")
        self.assertEqual(inspect_magic(b"unknown"), "application/octet-stream")

    def test_subprocess_runtime_is_capped(self):
        self.assertEqual(subprocess_timeout(9999, maximum=60), 60)
        with self.assertRaises(ValueError):
            subprocess_timeout(0)

    def test_systemd_hardening_contract(self):
        text = "\n".join(SYSTEMD_HARDENING)
        for directive in ("NoNewPrivileges", "PrivateTmp", "ProtectSystem", "CapabilityBoundingSet"):
            self.assertIn(directive, text)
