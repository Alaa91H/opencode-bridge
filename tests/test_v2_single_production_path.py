import json
import unittest
from pathlib import Path


class SingleProductionPathTests(unittest.TestCase):
    def test_canonical_opencode_config_exists(self):
        canonical = json.loads(Path("opencode.json").read_text())
        self.assertEqual(canonical["server"]["hostname"], "127.0.0.1")

    def test_legacy_v3_is_semantically_identical_compatibility_artifact(self):
        canonical = json.loads(Path("opencode.json").read_text())
        legacy = json.loads(Path("opencode-v3.json").read_text())
        self.assertEqual(legacy, canonical)

    def test_ci_validates_only_canonical_config(self):
        workflow = Path(".github/workflows/ci.yml").read_text()
        self.assertIn("python -m json.tool opencode.json", workflow)
        self.assertNotIn("python -m json.tool opencode-v3.json", workflow)
