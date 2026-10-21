"""Prevent accidental removal of the inter-deployment exclusion guard."""

from __future__ import annotations

import unittest
from pathlib import Path

DEPLOY_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy.sh"


class DeploymentLockTests(unittest.TestCase):
    def test_release_activation_is_serialized_before_repository_mutation(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        lock = source.index("flock -n 9")
        checkout = source.index('git checkout --detach --quiet "$TARGET_COMMIT"')
        self.assertLess(lock, checkout)
        self.assertIn('readonly DEPLOY_LOCK="${DEPLOY_STATE_DIR}/deploy.lock"', source)
        self.assertIn('if [[ -L "$DEPLOY_LOCK" ]]; then', source)
        self.assertIn("exit 75", source)


if __name__ == "__main__":
    unittest.main()
