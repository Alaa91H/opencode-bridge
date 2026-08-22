from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_DIR / "maintenance" / "daily-maintenance.sh"
SERVICE = PROJECT_DIR / "maintenance" / "opencode-bridge-maintenance.service"
TIMER = PROJECT_DIR / "maintenance" / "opencode-bridge-maintenance.timer"
GUARD = PROJECT_DIR / "maintenance" / "reboot-guard.sh"
GUARD_SERVICE = PROJECT_DIR / "maintenance" / "opencode-bridge-reboot-guard.service"
INSTALLER = PROJECT_DIR / "maintenance" / "install-root-assets.sh"


class MaintenanceAssetTests(unittest.TestCase):
    def test_routine_maintenance_is_silent_and_limited(self) -> None:
        content = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Routine maintenance is deliberately silent", content)
        self.assertNotIn("sendMessage", content)
        self.assertNotIn("api.telegram.org", content)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD", content)
        self.assertNotIn("rm -rf", content)
        self.assertIn("maintenance-latest.md", content)
        self.assertIn('ATTACHMENT_ROOT="${RUNTIME_DIR}/attachments"', content)
        self.assertIn("ATTACHMENT_RETENTION_MINUTES=10080", content)
        self.assertIn('find "$ATTACHMENT_ROOT" -xdev -depth -type f -mmin +"$ATTACHMENT_RETENTION_MINUTES" -delete', content)
        self.assertIn("systemctl start --no-block opencode-bridge-reboot-guard.service", content)

    def test_reboot_guard_waits_and_checks_queue(self) -> None:
        content = GUARD.read_text(encoding="utf-8")
        self.assertIn('WAIT_SECONDS="${REBOOT_WAIT_SECONDS:-300}"', content)
        self.assertIn('callback_data":"reboot:now"', content)
        self.assertIn('callback_data":"reboot:cancel"', content)
        self.assertIn('"${BRIDGE_DIR}/scripts/check_queue.py"', content)
        self.assertIn("deferred_running_task", content)
        self.assertIn("reboot request could not be delivered", content)
        self.assertIn('chown ubuntu:ubuntu "$REQUEST_PATH"', content)

    def test_root_units_and_installer_have_required_boundaries(self) -> None:
        for path in (SERVICE, GUARD_SERVICE):
            content = path.read_text(encoding="utf-8")
            self.assertIn("User=root", content)
            self.assertIn("NoNewPrivileges=yes", content)
            self.assertIn("PrivateTmp=yes", content)
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("opencode-bridge-reboot-guard", installer)
        self.assertIn("systemctl daemon-reload", installer)

    def test_timer_is_persistent_daily_schedule(self) -> None:
        content = TIMER.read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 08:30:00 UTC", content)
        self.assertIn("Persistent=true", content)
        self.assertIn("RandomizedDelaySec=20m", content)


if __name__ == "__main__":
    unittest.main()
