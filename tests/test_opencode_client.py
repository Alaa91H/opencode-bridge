from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from opencode_client import message_model_reference


class OpenCodeClientModelTests(unittest.TestCase):
    def test_message_model_reference_converts_persisted_model_id(self) -> None:
        self.assertEqual(
            message_model_reference("opencode/muse-spark-1.2-contributor-free"),
            {"providerID": "opencode", "modelID": "muse-spark-1.2-contributor-free"},
        )

    def test_message_model_reference_accepts_valid_object(self) -> None:
        self.assertEqual(
            message_model_reference({"providerID": "opencode", "modelID": "muse-spark-1.2-contributor-free"}),
            {"providerID": "opencode", "modelID": "muse-spark-1.2-contributor-free"},
        )

    def test_message_model_reference_rejects_invalid_value(self) -> None:
        with self.assertRaises(ValueError):
            message_model_reference("not-a-qualified-model")


if __name__ == "__main__":
    unittest.main()
