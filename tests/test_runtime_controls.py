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
            self.assertTrue(c.auto_update())
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
