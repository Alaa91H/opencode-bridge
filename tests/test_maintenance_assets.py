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
    def test_deferred_task_files_survive_daily_retention_cleanup(self):
        import os
        import shlex
        import sqlite3
        import subprocess
        import sys
        import tempfile
        import time

        content = SCRIPT.read_text(encoding="utf-8")
        start = content.index("cleanup_managed_attachments()")
        end = content.index('\nrecord_step ', start)
        function = content[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attachments = root / "runtime" / "attachments"
            attachments.mkdir(parents=True)
            saved = attachments / "saved-input.txt"
            saved.write_text("required by suspended task")
            old = time.time() - 10 * 86400
            os.utime(saved, (old, old))
            with sqlite3.connect(root / "sessions.db") as db:
                db.execute("CREATE TABLE agent_tasks(status TEXT)")
                db.execute("INSERT INTO agent_tasks VALUES ('retrying')")
            script = (
                f"BRIDGE_DIR={shlex.quote(str(root))}\n"
                f"PYTHON_BIN={shlex.quote(sys.executable)}\n"
                f"ATTACHMENT_ROOT={shlex.quote(str(attachments))}\n"
                'run_as_bridge_user() { "$@"; }\n'
                'resolve_retention_minutes() { echo 1440; }\n'
                + function + '\ncleanup_managed_attachments\n'
            )
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(saved.exists())
            with sqlite3.connect(root / "sessions.db") as db:
                db.execute("UPDATE agent_tasks SET status='completed'")
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(saved.exists())

    def test_routine_maintenance_is_silent_and_limited(self) -> None:
        content = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Routine maintenance is deliberately silent", content)
        self.assertNotIn("sendMessage", content)
        self.assertNotIn("api.telegram.org", content)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD", content)
        self.assertNotIn("rm -rf", content)
        self.assertIn("maintenance-latest.md", content)
        self.assertIn('ATTACHMENT_ROOT="${RUNTIME_DIR}/attachments"', content)
        # The retention window comes from the owner's stored preference; the
        # constant is only the fallback and must stay at the documented 7 days.
        self.assertIn("ATTACHMENT_RETENTION_DEFAULT_DAYS=7", content)
        self.assertIn('ATTACHMENT_RETENTION_DAYS_FILE="${RUNTIME_DIR}/retention-days"', content)
        self.assertIn("resolve_retention_minutes", content)
        self.assertIn("raw >= 1 && raw <= 365", content)
        self.assertNotIn("ATTACHMENT_RETENTION_MINUTES=10080", content)
        self.assertIn('find "$ATTACHMENT_ROOT" -xdev -depth -type f -mmin +"$retention_minutes" -delete', content)
        self.assertIn("systemctl start --no-block opencode-bridge-reboot-guard.service", content)

    def test_retention_resolution_accepts_a_valid_window_and_rejects_junk(self) -> None:
        import subprocess
        import tempfile

        content = SCRIPT.read_text(encoding="utf-8")
        start = content.index("resolve_retention_minutes()")
        end = content.index("cleanup_managed_attachments()")
        function = content[start:end]
        function = function.replace('readonly ATTACHMENT_RETENTION_DEFAULT_DAYS=7', "")
        function = function.replace(
            'readonly ATTACHMENT_RETENTION_DAYS_FILE="${RUNTIME_DIR}/retention-days"', ""
        )
        function = function.replace(
            'echo "قيمة مدة الاحتفاظ غير صالحة؛ استخدام الافتراضي ${ATTACHMENT_RETENTION_DEFAULT_DAYS} يوم." >&2',
            'echo invalid >&2',
        )
        function = function.replace('ATTACHMENT_RETENTION_DEFAULT_DAYS', 'DEFAULT_DAYS')
        function = function.replace('ATTACHMENT_RETENTION_DAYS_FILE', 'DAYS_FILE')

        cases = {
            "": 10080,
            "1": 1440,
            "7": 10080,
            "30": 43200,
            "365": 525600,
            "0": 10080,
            "366": 10080,
            "abc": 10080,
            "14d": 10080,
        }
        for value, expected in cases.items():
            with self.subTest(value=value), tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
                handle.write(value + "\n")
                path = handle.name
            try:
                script = (
                    f'DEFAULT_DAYS=7\nDAYS_FILE="{path}"\n' + function + '\nresolve_retention_minutes\n'
                )
                result = subprocess.run(
                    ["bash", "-c", script], capture_output=True, text=True, timeout=15
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip().splitlines()[-1], str(expected))
            finally:
                Path(path).unlink(missing_ok=True)

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
