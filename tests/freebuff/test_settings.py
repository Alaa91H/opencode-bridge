from __future__ import annotations

import unittest
from pathlib import Path

from bridge.config.settings import BridgeSettings, SettingsError

MISSING_ENV_FILE = Path("/nonexistent/bridge-test.env")


def _env(**extra: str) -> dict[str, str]:
    values = {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "1",
    }
    values.update(extra)
    return values


def _load(**extra: str) -> BridgeSettings:
    return BridgeSettings.load(env=_env(**extra), env_file=MISSING_ENV_FILE)


class FreebuffSettingsTests(unittest.TestCase):
    def test_backend_defaults_to_opencode(self) -> None:
        settings = _load()
        self.assertEqual(settings.agent.backend, "opencode")
        self.assertTrue(settings.freebuff.workspace == "")
        self.assertFalse(settings.freebuff.model_write)
        self.assertFalse(settings.freebuff.trust_agents)

    def test_freebuff_values_are_parsed(self) -> None:
        settings = _load(
            AGENT_BACKEND="freebuff",
            FREEBUFF_BIN="/opt/freebuff",
            FREEBUFF_CONFIG_DIR="/home/ubuntu/.config/manicode",
            FREEBUFF_WORKSPACE="/home/ubuntu/github-workspaces",
            FREEBUFF_MODEL="model-a",
            FREEBUFF_MODELS="model-a,model-b",
            FREEBUFF_MODEL_WRITE="1",
            FREEBUFF_IDLE_SECONDS="30",
            FREEBUFF_TIMEOUT_SECONDS="7200",
            FREEBUFF_MAX_OUTPUT_CHARS="90000",
        )
        self.assertEqual(settings.agent.backend, "freebuff")
        self.assertEqual(settings.freebuff.bin, "/opt/freebuff")
        self.assertEqual(settings.freebuff.models, ("model-a", "model-b"))
        self.assertTrue(settings.freebuff.model_write)
        self.assertEqual(settings.freebuff.idle_seconds, 30.0)
        self.assertEqual(settings.freebuff.timeout_seconds, 7200.0)
        self.assertEqual(settings.freebuff.max_output_chars, 90000)

    def test_invalid_backend_is_rejected(self) -> None:
        with self.assertRaises(SettingsError):
            _load(AGENT_BACKEND="unknown")

    def test_timeout_must_cover_startup(self) -> None:
        with self.assertRaises(SettingsError):
            _load(FREEBUFF_STARTUP_SECONDS="100", FREEBUFF_TIMEOUT_SECONDS="60")


if __name__ == "__main__":
    unittest.main()
