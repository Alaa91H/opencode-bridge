from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge.services.runtime_controls import RuntimeControls


class RuntimeControlsTests(unittest.TestCase):
    def test_persistent_switch_and_memory_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            c = RuntimeControls(Path(temp))
            self.assertFalse(c.auto_update())  # Explicit opt-in required
            c.set_auto_update(False)
            self.assertFalse(RuntimeControls(Path(temp)).auto_update())
            self.assertEqual(c.memory(), {"zram_percent": "auto", "swap_gib": "auto"})
            self.assertEqual(c.set_memory(zram_percent=75, swap_gib=2),
                             {"zram_percent": 75, "swap_gib": 2})
            self.assertEqual(RuntimeControls(Path(temp)).memory()["swap_gib"], 2)
            with self.assertRaises(ValueError):
                c.set_memory(zram_percent=500)
            self.assertEqual(c.memory()["zram_percent"], 75)


    def test_quarantined_update_is_visible_but_invalid_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controls = RuntimeControls(root)
            self.assertIsNone(controls.failed_update_release())
            path = root / "auto-update-quarantine.json"
            path.write_text(json.dumps({
                "schema": 1, "tag": "v3.3.0", "reason": "CalledProcessError",
            }), encoding="utf-8")
            self.assertEqual(controls.failed_update_release(), "v3.3.0")
            path.write_text(json.dumps({"schema": 1, "tag": "../unsafe"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                controls.failed_update_release()
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                controls.failed_update_release()


    def test_autopilot_modes_are_durable_and_legacy_switch_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controls = RuntimeControls(root)
            self.assertEqual(controls.autopilot_mode(), "off")
            for mode, deploys in (("notify", False), ("off", False),
                                  ("auto-safe", True), ("maintenance-window", True),
                                  ("emergency-stop", False)):
                with self.subTest(mode=mode):
                    controls.set_autopilot_mode(mode)
                    refreshed = RuntimeControls(root)
                    self.assertEqual(refreshed.autopilot_mode(), mode)
                    self.assertEqual(refreshed.auto_update(), deploys)
            controls.set_auto_update(False)
            self.assertEqual(RuntimeControls(root).autopilot_mode(), "off")
            self.assertEqual((root / "auto-update-enabled").read_text().strip(), "0")
            controls.set_auto_update(True)
            self.assertEqual(RuntimeControls(root).autopilot_mode(), "auto-safe")
            with self.assertRaises(ValueError):
                controls.set_autopilot_mode("unrestricted-root")
            self.assertEqual(controls.autopilot_mode(), "auto-safe")

    def test_emergency_stop_blocks_new_os_opt_in_and_disables_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            controls = RuntimeControls(Path(temp))
            controls.set_autopilot_mode("auto-safe")
            controls.set_system_auto_update(True)
            controls.set_autopilot_mode("emergency-stop")
            self.assertFalse(controls.auto_update())
            self.assertEqual((Path(temp) / "auto-update-enabled").read_text().strip(), "0")
            with self.assertRaises(ValueError):
                controls.set_system_auto_update(True)
            self.assertTrue(controls.system_auto_update(),
                            "Existing host update switch remains stored, but root gate must pause it")
            controls.set_autopilot_mode("off")
            controls.set_system_auto_update(False)
            self.assertFalse(controls.system_auto_update())

    def test_interrupted_policy_write_disables_legacy_updater_first(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            controls = RuntimeControls(Path(temp))
            controls.set_autopilot_mode("auto-safe")
            def interrupt(path, payload):
                if Path(path).name == "autopilot-mode":
                    raise OSError("simulated power interruption")
                from bridge.services.host_credentials import secure_write
                secure_write(path, payload)
            with patch("bridge.services.host_credentials.secure_write", side_effect=interrupt):
                with self.assertRaises(OSError):
                    controls.set_autopilot_mode("emergency-stop")
            self.assertEqual((Path(temp) / "auto-update-enabled").read_text().strip(), "0")

    def test_invalid_autopilot_mode_is_not_treated_as_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "autopilot-mode").write_text("unrecognized\n", encoding="ascii")
            with self.assertRaises(ValueError):
                RuntimeControls(root).auto_update()


    def test_last_release_operation_survives_controls_restart(self):
        from bridge.services.update_coordinator import UpdateCoordinator

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(RuntimeControls(root).last_release_operation())
            sha = "e" * 40
            UpdateCoordinator(root).transition("v3.3.0", sha, "verified")
            last = RuntimeControls(root).last_release_operation()
            self.assertEqual(last.phase, "verified")
            self.assertEqual(last.tag, "v3.3.0")
            UpdateCoordinator(root).transition("v3.3.0", sha, "quarantined")
            self.assertEqual(RuntimeControls(root).last_release_operation().phase,
                             "quarantined")


    def test_legacy_enable_file_preserves_explicit_owner_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "auto-update-enabled"
            legacy.write_text("1\n", encoding="ascii")
            self.assertTrue(RuntimeControls(root).auto_update())
            legacy.write_text("0\n", encoding="ascii")
            self.assertFalse(RuntimeControls(root).auto_update())
