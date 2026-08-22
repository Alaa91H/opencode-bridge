from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_DIR / "maintenance" / "daily-maintenance.sh"
SERVICE = PROJECT_DIR / "maintenance" / "opencode-bridge-maintenance.service"
TIMER = PROJECT_DIR / "maintenance" / "opencode-bridge-maintenance.timer"


class MaintenanceAssetTests(unittest.TestCase):
    def test_script_has_explicit_safety_boundaries(self) -> None:
        content = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("never reboots", content)
        self.assertNotRegex(content, r"(?m)^\s*(?:sudo\s+)?(?:systemctl\s+)?reboot\b")
        self.assertNotIn("rm -rf", content)
        self.assertIn("sendMessage", content)
        self.assertIn("maintenance-latest.md", content)
        self.assertIn('ATTACHMENT_ROOT="${RUNTIME_DIR}/attachments"', content)
        self.assertIn("ATTACHMENT_RETENTION_MINUTES=10080", content)
        self.assertIn('find "$ATTACHMENT_ROOT" -xdev -depth -type f -mmin +"$ATTACHMENT_RETENTION_MINUTES" -delete', content)

    def test_service_uses_root_and_resource_boundaries(self) -> None:
        content = SERVICE.read_text(encoding="utf-8")
        self.assertIn("User=root", content)
        self.assertIn("NoNewPrivileges=yes", content)
        self.assertIn("PrivateTmp=yes", content)
        self.assertNotIn("ProtectSystem=full", content)

    def test_timer_is_persistent_daily_schedule(self) -> None:
        content = TIMER.read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 08:30:00 UTC", content)
        self.assertIn("Persistent=true", content)
        self.assertIn("RandomizedDelaySec=20m", content)


if __name__ == "__main__":
    unittest.main()
