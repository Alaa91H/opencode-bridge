from __future__ import annotations

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
            self.assertEqual(c.memory(), {"zram_percent": 50, "swap_gib": "auto"})
            self.assertEqual(c.set_memory(zram_percent=75, swap_gib=2),
                             {"zram_percent": 75, "swap_gib": 2})
            self.assertEqual(RuntimeControls(Path(temp)).memory()["swap_gib"], 2)
            with self.assertRaises(ValueError):
                c.set_memory(zram_percent=500)
            self.assertEqual(c.memory()["zram_percent"], 75)
