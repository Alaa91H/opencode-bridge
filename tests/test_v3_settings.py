from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge.config import BridgeSettings, SettingsError


class BridgeSettingsTests(unittest.TestCase):
    def test_defaults_are_typed_and_valid(self) -> None:
        settings = BridgeSettings.load(env={})
        self.assertEqual(settings.opencode.host, "127.0.0.1")
        self.assertEqual(settings.opencode.port, 4096)
        self.assertEqual(settings.agent.task_workers, 3)
        self.assertTrue(settings.features.adaptive_workers)

    def test_environment_overrides_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge.toml"
            path.write_text(
                """
[agent]
task_workers = 3

[opencode]
port = 5000
""".strip(),
                encoding="utf-8",
            )
            settings = BridgeSettings.load(
                env={
                    "AGENT_TASK_WORKERS": "5",
                    "OPENCODE_PORT": "6000",
                },
                config_path=path,
            )
        self.assertEqual(settings.agent.task_workers, 3)
        self.assertEqual(settings.opencode.port, 6000)

    def test_env_file_is_fallback_below_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "AGENT_TASK_WORKERS=3\nOPENCODE_PORT=5000\n",
                encoding="utf-8",
            )
            settings = BridgeSettings.load(
                env={"AGENT_TASK_WORKERS": "4"},
                env_file=env_file,
            )
        self.assertEqual(settings.agent.task_workers, 3)
        self.assertEqual(settings.opencode.port, 5000)

    def test_json_config_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge.json"
            path.write_text(
                json.dumps(
                    {
                        "telegram": {"attachment_max_count": 15},
                        "features": {"adaptive_workers": False},
                    }
                ),
                encoding="utf-8",
            )
            settings = BridgeSettings.load(env={}, config_path=path)
        self.assertEqual(settings.telegram.attachment_max_count, 15)
        self.assertFalse(settings.features.adaptive_workers)

    def test_unknown_config_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge.toml"
            path.write_text(
                "[telegram]\nunknown_option = 1\n",
                encoding="utf-8",
            )
            with self.assertRaises(SettingsError):
                BridgeSettings.load(env={}, config_path=path)

    def test_invalid_cross_field_limit_is_rejected(self) -> None:
        with self.assertRaises(SettingsError):
            BridgeSettings.load(
                env={
                    "TELEGRAM_ATTACHMENT_MAX_BYTES": "100",
                    "TELEGRAM_ATTACHMENT_MAX_TOTAL_BYTES": "50",
                }
            )

    def test_user_policy_then_task_override_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge.json"
            path.write_text(
                json.dumps(
                    {
                        "users": {
                            "42": {
                                "agent": {"task_workers": 3},
                                "opencode": {"default_model": "user/model"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            settings = BridgeSettings.load(env={}, config_path=path)
        user = settings.resolve_for("42")
        self.assertEqual(user.agent.task_workers, 3)
        self.assertEqual(user.opencode.default_model, "user/model")
        task = settings.resolve_for(
            "42",
            task_override={
                "agent": {"task_workers": 4},
                "opencode": {"default_model": "task/model"},
            },
        )
        self.assertEqual(task.agent.task_workers, 3)
        self.assertEqual(task.opencode.default_model, "task/model")

    def test_secret_or_admin_override_is_rejected(self) -> None:
        settings = BridgeSettings.load(env={})
        with self.assertRaises(SettingsError):
            settings.resolve_for(
                "42",
                task_override={"opencode": {"password": "secret"}},
            )
        with self.assertRaises(SettingsError):
            settings.resolve_for(
                "42",
                task_override={"telegram": {"allowed_users": [42]}},
            )

    def test_public_dict_redacts_secrets(self) -> None:
        settings = BridgeSettings.load(
            env={
                "TELEGRAM_BOT_TOKEN": "token",
                "OPENCODE_PASSWORD": "password",
                "GITHUB_TOKEN": "github",
                "OPENCODE_CREDENTIALS": "primary=secret-a;backup=secret-b",
            }
        )
        public = settings.public_dict()
        self.assertEqual(public["telegram"]["bot_token"], "<redacted>")
        self.assertEqual(public["opencode"]["password"], "<redacted>")
        self.assertEqual(public["opencode"]["credentials"], "<redacted>")
        self.assertNotIn("secret-a", repr(public))
        self.assertEqual(public["github"]["token"], "<redacted>")
        self.assertNotIn("user_policies", public)

    def test_bot_readiness_requires_token_and_allowlist(self) -> None:
        settings = BridgeSettings.load(env={})
        with self.assertRaises(SettingsError):
            settings.require_bot_ready()
        settings = BridgeSettings.load(
            env={
                "TELEGRAM_BOT_TOKEN": "token",
                "TELEGRAM_ALLOWED_USERS": "1,2",
            }
        )
        settings.require_bot_ready()

    def test_effective_pin_respects_auto_strongest_flag(self) -> None:
        auto = BridgeSettings.load(
            env={
                "OPENCODE_PIN_DEFAULT_MODEL": "1",
                "AGENT_SCOUT_AUTO_STRONGEST": "1",
            }
        )
        self.assertFalse(auto.effective_pin_default_model)
        pinned = BridgeSettings.load(
            env={
                "OPENCODE_PIN_DEFAULT_MODEL": "1",
                "AGENT_SCOUT_AUTO_STRONGEST": "0",
            }
        )
        self.assertTrue(pinned.effective_pin_default_model)


if __name__ == "__main__":
    unittest.main()
