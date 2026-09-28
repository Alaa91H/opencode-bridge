import json
import tempfile
import unittest
from pathlib import Path

from scripts.release_readiness import REQUIRED, validate


class ReleaseReadinessTests(unittest.TestCase):
    def test_current_evidence_is_fail_closed_until_real_soak(self):
        failures = validate(Path("release-evidence-2.0.json"))
        self.assertIn("soak_24_72h", failures)
        self.assertIn("soak_completed", failures)

    def test_complete_evidence_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            data = {name: True for name in REQUIRED}
            data["high_critical_vulnerabilities_accepted_or_zero"] = True
            data["soak"] = {"completed": True, "duration_hours": 24}
            path.write_text(json.dumps(data))
            self.assertEqual(validate(path), [])

    def test_short_or_overlong_soak_is_rejected(self):
        for hours in (23.99, 73):
            with self.subTest(hours=hours), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "evidence.json"
                data = {name: True for name in REQUIRED}
                data["high_critical_vulnerabilities_accepted_or_zero"] = True
                data["soak"] = {"completed": True, "duration_hours": hours}
                path.write_text(json.dumps(data))
                self.assertIn("soak_24_72h", validate(path))
