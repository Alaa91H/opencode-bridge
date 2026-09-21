from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPTIMIZER = ROOT / "maintenance" / "resource-optimizer.sh"
INSTALLER = ROOT / "maintenance" / "install-root-assets.sh"
SERVICE = ROOT / "maintenance" / "opencode-bridge-resource-optimizer.service"


class ZramDeploymentTests(unittest.TestCase):
    def test_default_zram_is_half_ram_without_legacy_2g_cap(self) -> None:
        source = OPTIMIZER.read_text(encoding="utf-8")
        self.assertIn('ZRAM_PERCENT="${OPENCODE_ZRAM_PERCENT:-50}"', source)
        self.assertIn('ZRAM_MAX_MIB="${OPENCODE_ZRAM_MAX_MIB:-0}"', source)
        self.assertIn('mem_kib * zram_percent / 100 / 1024', source)
        self.assertNotIn('OPENCODE_ZRAM_MAX_MIB:-2048', source)

    def test_installer_enables_resource_optimizer(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("resource-optimizer.sh", source)
        self.assertIn("opencode-bridge-resource-optimizer.service", source)
        self.assertIn("systemctl enable opencode-bridge-resource-optimizer.service", source)
        self.assertIn("systemctl restart opencode-bridge-resource-optimizer.service", source)

    def test_optimizer_is_boot_enabled_system_service(self) -> None:
        source = SERVICE.read_text(encoding="utf-8")
        self.assertIn("WantedBy=multi-user.target", source)
        self.assertIn("ExecStart=/usr/local/sbin/opencode-bridge-resource-optimizer", source)


if __name__ == "__main__":
    unittest.main()
