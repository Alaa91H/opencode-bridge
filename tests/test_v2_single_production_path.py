import json
import unittest
from pathlib import Path


class SingleProductionPathTests(unittest.TestCase):
    def test_canonical_opencode_config_exists(self):
        canonical = json.loads(Path("opencode.json").read_text())
        self.assertEqual(canonical["server"]["hostname"], "127.0.0.1")

    def test_legacy_v3_config_is_removed(self):
        self.assertFalse(Path("opencode-v3.json").exists())

    def test_ci_validates_only_canonical_config(self):
        workflow = Path(".github/workflows/ci.yml").read_text()
        self.assertIn("python -m json.tool opencode.json", workflow)
        self.assertNotIn("opencode-v3.json", workflow)
