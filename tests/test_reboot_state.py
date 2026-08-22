from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reboot_state import decision_path, read_state, remove_state, request_path, write_state


class RebootStateTests(unittest.TestCase):
    def test_state_round_trip_uses_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            request = request_path(runtime)
            decision = decision_path(runtime)
            self.assertEqual(request.name, "reboot-request.json")
            self.assertEqual(decision.name, "reboot-decision.json")
            write_state(request, {"request_id": "r-1", "status": "awaiting"})
            self.assertEqual(read_state(request), {"request_id": "r-1", "status": "awaiting"})
            write_state(decision, {"action": "cancel", "request_id": "r-1"})
            self.assertEqual(read_state(decision), {"action": "cancel", "request_id": "r-1"})
            remove_state(decision)
            self.assertIsNone(read_state(decision))


if __name__ == "__main__":
    unittest.main()
