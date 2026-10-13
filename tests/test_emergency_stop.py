"""Validate emergency stop gates without executing privileged maintenance."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.services.server_admin import update_now

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is needed for maintenance contract checks")
class EmergencyMaintenanceGateTests(unittest.TestCase):
    def test_root_maintenance_pauses_before_mutations(self) -> None:
        script = (ROOT / "maintenance" / "daily-maintenance.sh").read_text(
            encoding="utf-8",
        )
        start = script.index("# Honor the owner's emergency stop")
        end = script.index("STARTED_AT=", start)
        guard = script[start:end]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mode = root / "autopilot-mode"

            def run_gate() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [str(BASH), "-e", "-c", guard + "\n"
                     + "printf 'mutation_gate_passed\\n'"],
                    env={**os.environ, "RUNTIME_DIR": temp},
                    capture_output=True, text=True, timeout=10, check=False,
                )

            self.assertIn("mutation_gate_passed", run_gate().stdout)
            mode.write_text("emergency-stop\n", encoding="ascii")
            stopped = run_gate()
            self.assertEqual(stopped.returncode, 0)
            self.assertNotIn("mutation_gate_passed", stopped.stdout)
            self.assertIn("emergency-stop", stopped.stdout)
            mode.write_text("auto-safe\n", encoding="ascii")
            self.assertIn("mutation_gate_passed", run_gate().stdout)
            mode.write_text("not-a-policy\n", encoding="ascii")
            invalid = run_gate()
            self.assertNotEqual(invalid.returncode, 0)
            self.assertNotIn("mutation_gate_passed", invalid.stdout)
            mode.unlink()
            try:
                mode.symlink_to(root / "other-mode")
            except (OSError, NotImplementedError):
                return
            (root / "other-mode").write_text("auto-safe\n", encoding="ascii")
            linked = run_gate()
            self.assertNotEqual(linked.returncode, 0)
            self.assertNotIn("mutation_gate_passed", linked.stdout)


class EmergencyManualUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_manual_update_runs_subprocesses_during_emergency(self) -> None:
        with patch(
            "bridge.services.runtime_controls.RuntimeControls.autopilot_mode",
            return_value="emergency-stop",
        ), patch(
            "bridge.services.server_admin.asyncio.create_subprocess_exec",
            side_effect=AssertionError("subprocess must not run"),
        ):
            for target in ("agent", "system", "bridge"):
                with self.subTest(target=target):
                    message = await update_now(target)
                    self.assertIn("emergency stop", message)

    async def test_invalid_policy_blocks_manual_mutation(self) -> None:
        with patch(
            "bridge.services.runtime_controls.RuntimeControls.autopilot_mode",
            side_effect=ValueError("invalid policy"),
        ), patch(
            "bridge.services.server_admin.asyncio.create_subprocess_exec",
            side_effect=AssertionError("subprocess must not run"),
        ):
            message = await update_now("system")
            self.assertIn("invalid owner update policy", message)


if __name__ == "__main__":
    unittest.main()
