"""Typed, validated configuration for OpenCode Bridge.

Precedence:
defaults < config file < .env fallback < process environment
         < per-user policy < per-task override.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "bridge.toml"

_FALSE = {"0", "false", "no", "off", "disabled"}
_TRUE = {"1", "true", "yes", "on", "enabled"}


class SettingsError(ValueError):
    """Raised when bridge configuration is invalid or contradictory."""


def _parse_env_file(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _parse_bool(value: Any, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise SettingsError(f"{key} must be a boolean value")


def _parse_int(
    value: Any,
    *,
    key: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{key} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise SettingsError(f"{key} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise SettingsError(f"{key} must be <= {maximum}")
    return parsed


def _parse_float(
    value: Any,
    *,
    key: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{key} must be numeric") from exc
    if minimum is not None and parsed < minimum:
        raise SettingsError(f"{key} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise SettingsError(f"{key} must be <= {maximum}")
    return parsed


def _csv(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).split(",")
    return tuple(str(item).strip() for item in items if str(item).strip())


def _csv_int(value: Any, *, key: str) -> frozenset[int]:
    result: set[int] = set()
    for item in _csv(value):
        try:
            result.add(int(item))
        except ValueError as exc:
            raise SettingsError(f"{key} contains a non-integer identifier") from exc
    return frozenset(result)


def _nested(config: Mapping[str, Any], path: str, default: Any) -> Any:
    current: Any = config
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def _source_value(
    env: Mapping[str, str],
    config: Mapping[str, Any],
    env_name: str,
    config_path: str,
    default: Any,
) -> Any:
    if env_name in env:
        return env[env_name]
    return _nested(config, config_path, default)


def _load_config_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix.lower() in {".toml", ".tml"}:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        else:
            raise SettingsError("BRIDGE_CONFIG_FILE must be TOML or JSON")
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SettingsError(f"failed to read configuration file: {path}") from exc
    if not isinstance(data, dict):
        raise SettingsError("configuration root must be an object/table")
    return data


_ALLOWED_SECTION_KEYS: dict[str, set[str]] = {
    "telegram": {
        "bot_token",
        "allowed_users",
        "allowed_chat_ids",
        "proxy_url",
        "api_mode",
        "local_api_base_url",
        "local_file_base_url",
        "attachment_max_bytes",
        "attachment_max_count",
        "attachment_max_total_bytes",
        "attachment_pending_seconds",
        "media_group_debounce_seconds",
    },
    "opencode": {
        "host",
        "port",
        "password",
        "server_username",
        "server_password",
        "default_model",
        "model_variant",
        "variant_model",
        "pin_default_model",
        "agent",
        "model_sync_seconds",
    },
    "agent": {
        "scout_interval_seconds",
        "scout_preferred_agent",
        "v3_agent",
        "task_workers",
        "task_poll_seconds",
        "worker_recovery_seconds",
    },
    "workspace": {"root", "allowed_repos"},
    "github": {"token", "ci_workflow"},
    "watchdog": {
        "interval_seconds",
        "stale_task_seconds",
        "restart_cooldown_seconds",
        "max_restarts_per_hour",
    },
    "logging": {"level"},
    "features": {
        "auto_strongest_free_model",
        "adaptive_workers",
        "scout_web_research",
    },
    "users": set(),
}


def _validate_config_keys(config: Mapping[str, Any]) -> None:
    unknown_sections = set(config) - set(_ALLOWED_SECTION_KEYS)
    if unknown_sections:
        raise SettingsError(
            "unknown configuration section(s): "
            + ", ".join(sorted(unknown_sections))
        )
    for section, allowed in _ALLOWED_SECTION_KEYS.items():
        if section not in config or section == "users":
            continue
        value = config[section]
        if not isinstance(value, Mapping):
            raise SettingsError(f"{section} must be a table/object")
        unknown = set(value) - allowed
        if unknown:
            raise SettingsError(
                f"unknown {section} setting(s): " + ", ".join(sorted(unknown))
            )
    users = config.get("users", {})
    if users and not isinstance(users, Mapping):
        raise SettingsError("users must be a table/object")


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str = ""
    allowed_users: frozenset[int] = frozenset()
    allowed_chat_ids: frozenset[int] = frozenset()
    proxy_url: str | None = None
    api_mode: str = "cloud"
    local_api_base_url: str = "http://127.0.0.1:8081/bot"
    local_file_base_url: str = "http://127.0.0.1:8081/file/bot"
    attachment_max_bytes: int = 20 * 1024 * 1024
    attachment_max_count: int = 10
    attachment_max_total_bytes: int = 50 * 1024 * 1024
    attachment_pending_seconds: int = 600
    media_group_debounce_seconds: float = 1.25


@dataclass(frozen=True)
class OpenCodeSettings:
    host: str = "127.0.0.1"
    port: int = 4096
    password: str | None = None
    server_username: str = "opencode"
    server_password: str | None = None
    default_model: str = "opencode/muse-spark-1.3-contributor-free"
    model_variant: str | None = "xhigh"
    variant_model: str = "opencode/muse-spark-1.3-contributor-free"
    pin_default_model: bool = False
    agent: str = "telegram-operator"
    model_sync_seconds: int = 900


@dataclass(frozen=True)
class AgentSettings:
    scout_interval_seconds: float = 86400.0
    scout_preferred_agent: str = "development-agent"
    v3_agent: str = "development-agent"
    task_workers: int = 2
    task_poll_seconds: float = 5.0
    worker_recovery_seconds: float = 30.0


@dataclass(frozen=True)
class WorkspaceSettings:
    root: Path = Path("/home/ubuntu/github-workspaces")
    allowed_repos: tuple[str, ...] = ()


@dataclass(frozen=True)
class GitHubSettings:
    token: str = ""
    ci_workflow: str | None = "CI"


@dataclass(frozen=True)
class WatchdogSettings:
    interval_seconds: int = 300
    stale_task_seconds: int = 7200
    restart_cooldown_seconds: int = 300
    max_restarts_per_hour: int = 3


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"


@dataclass(frozen=True)
class FeatureFlags:
    auto_strongest_free_model: bool = True
    adaptive_workers: bool = True
    scout_web_research: bool = True


@dataclass(frozen=True)
class BridgeSettings:
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    opencode: OpenCodeSettings = field(default_factory=OpenCodeSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    workspace: WorkspaceSettings = field(default_factory=WorkspaceSettings)
    github: GitHubSettings = field(default_factory=GitHubSettings)
    watchdog: WatchdogSettings = field(default_factory=WatchdogSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    user_policies: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    config_file: Path | None = None

    @classmethod
    def load(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        env_file: Path | None = None,
        config_path: Path | None = None,
    ) -> "BridgeSettings":
        fallback_env = _parse_env_file(env_file)
        process_env = dict(os.environ if env is None else env)
        merged_env = {**fallback_env, **process_env}

        resolved_config_path = config_path
        if resolved_config_path is None:
            configured = merged_env.get("BRIDGE_CONFIG_FILE", "").strip()
            if configured:
                resolved_config_path = Path(configured).expanduser()
            elif DEFAULT_CONFIG_FILE.is_file():
                resolved_config_path = DEFAULT_CONFIG_FILE

        config = _load_config_file(resolved_config_path)
        _validate_config_keys(config)

        telegram = TelegramSettings(
            bot_token=str(_source_value(
                merged_env, config, "TELEGRAM_BOT_TOKEN", "telegram.bot_token", ""
            )).strip(),
            allowed_users=_csv_int(
                _source_value(
                    merged_env, config, "TELEGRAM_ALLOWED_USERS",
                    "telegram.allowed_users", ()
                ),
                key="TELEGRAM_ALLOWED_USERS",
            ),
            allowed_chat_ids=_csv_int(
                _source_value(
                    merged_env, config, "TELEGRAM_ALLOWED_CHAT_IDS",
                    "telegram.allowed_chat_ids", ()
                ),
                key="TELEGRAM_ALLOWED_CHAT_IDS",
            ),
            proxy_url=(
                str(_source_value(
                    merged_env, config, "TELEGRAM_PROXY_URL",
                    "telegram.proxy_url", ""
                )).strip()
                or None
            ),
            api_mode=str(_source_value(
                merged_env, config, "TELEGRAM_API_MODE", "telegram.api_mode", "cloud"
            )).strip().lower(),
            local_api_base_url=str(_source_value(
                merged_env, config, "TELEGRAM_LOCAL_API_BASE_URL",
                "telegram.local_api_base_url", "http://127.0.0.1:8081/bot"
            )).strip().rstrip("/"),
            local_file_base_url=str(_source_value(
                merged_env, config, "TELEGRAM_LOCAL_FILE_BASE_URL",
                "telegram.local_file_base_url", "http://127.0.0.1:8081/file/bot"
            )).strip().rstrip("/"),
            attachment_max_bytes=_parse_int(
                _source_value(
                    merged_env, config, "TELEGRAM_ATTACHMENT_MAX_BYTES",
                    "telegram.attachment_max_bytes", 20 * 1024 * 1024
                ),
                key="TELEGRAM_ATTACHMENT_MAX_BYTES",
                minimum=1,
            ),
            attachment_max_count=_parse_int(
                _source_value(
                    merged_env, config, "TELEGRAM_ATTACHMENT_MAX_COUNT",
                    "telegram.attachment_max_count", 10
                ),
                key="TELEGRAM_ATTACHMENT_MAX_COUNT",
                minimum=1,
            ),
            attachment_max_total_bytes=_parse_int(
                _source_value(
                    merged_env, config, "TELEGRAM_ATTACHMENT_MAX_TOTAL_BYTES",
                    "telegram.attachment_max_total_bytes", 50 * 1024 * 1024
                ),
                key="TELEGRAM_ATTACHMENT_MAX_TOTAL_BYTES",
                minimum=1,
            ),
            attachment_pending_seconds=_parse_int(
                _source_value(
                    merged_env, config, "TELEGRAM_ATTACHMENT_PENDING_SECONDS",
                    "telegram.attachment_pending_seconds", 600
                ),
                key="TELEGRAM_ATTACHMENT_PENDING_SECONDS",
                minimum=60,
            ),
            media_group_debounce_seconds=_parse_float(
                _source_value(
                    merged_env, config, "TELEGRAM_MEDIA_GROUP_DEBOUNCE_SECONDS",
                    "telegram.media_group_debounce_seconds", 1.25
                ),
                key="TELEGRAM_MEDIA_GROUP_DEBOUNCE_SECONDS",
                minimum=0.25,
                maximum=5.0,
            ),
        )

        if telegram.api_mode not in {"cloud", "local"}:
            raise SettingsError("TELEGRAM_API_MODE must be cloud or local")
        if telegram.api_mode == "local" and (
            not telegram.local_api_base_url.startswith(("http://", "https://"))
            or not telegram.local_file_base_url.startswith(("http://", "https://"))
        ):
            raise SettingsError("local Telegram API URLs must be HTTP(S) URLs")

        default_model = str(_source_value(
            merged_env, config, "OPENCODE_DEFAULT_MODEL",
            "opencode.default_model", OpenCodeSettings.default_model
        )).strip()
        opencode = OpenCodeSettings(
            host=str(_source_value(
                merged_env, config, "OPENCODE_HOST", "opencode.host", "127.0.0.1"
            )).strip(),
            port=_parse_int(
                _source_value(
                    merged_env, config, "OPENCODE_PORT", "opencode.port", 4096
                ),
                key="OPENCODE_PORT", minimum=1, maximum=65535,
            ),
            password=(
                str(_source_value(
                    merged_env, config, "OPENCODE_PASSWORD",
                    "opencode.password", ""
                )).strip()
                or None
            ),
            server_username=str(_source_value(
                merged_env, config, "OPENCODE_SERVER_USERNAME",
                "opencode.server_username", "opencode"
            )).strip() or "opencode",
            server_password=(
                str(_source_value(
                    merged_env, config, "OPENCODE_SERVER_PASSWORD",
                    "opencode.server_password", ""
                )).strip()
                or None
            ),
            default_model=default_model,
            model_variant=(
                str(_source_value(
                    merged_env, config, "OPENCODE_MODEL_VARIANT",
                    "opencode.model_variant", "xhigh"
                )).strip()
                or None
            ),
            variant_model=str(_source_value(
                merged_env, config, "OPENCODE_VARIANT_MODEL",
                "opencode.variant_model", default_model
            )).strip() or default_model,
            pin_default_model=_parse_bool(
                _source_value(
                    merged_env, config, "OPENCODE_PIN_DEFAULT_MODEL",
                    "opencode.pin_default_model", False
                ),
                key="OPENCODE_PIN_DEFAULT_MODEL",
            ),
            agent=str(_source_value(
                merged_env, config, "OPENCODE_AGENT",
                "opencode.agent", "telegram-operator"
            )).strip() or "telegram-operator",
            model_sync_seconds=_parse_int(
                _source_value(
                    merged_env, config, "OPENCODE_MODEL_SYNC_SECONDS",
                    "opencode.model_sync_seconds", 900
                ),
                key="OPENCODE_MODEL_SYNC_SECONDS", minimum=60,
            ),
        )

        agent = AgentSettings(
            scout_interval_seconds=_parse_float(
                _source_value(
                    merged_env, config, "AGENT_SCOUT_INTERVAL_SECONDS",
                    "agent.scout_interval_seconds", 86400
                ),
                key="AGENT_SCOUT_INTERVAL_SECONDS", minimum=3600,
            ),
            scout_preferred_agent=str(_source_value(
                merged_env, config, "AGENT_SCOUT_PREFERRED_AGENT",
                "agent.scout_preferred_agent", "development-agent"
            )).strip() or "development-agent",
            v3_agent=str(_source_value(
                merged_env,
                config,
                "OPENCODE_AGENT",
                "agent.v3_agent",
                _nested(config, "opencode.agent", "development-agent"),
            )).strip() or "development-agent",
            task_workers=_parse_int(
                _source_value(
                    merged_env, config, "AGENT_TASK_WORKERS",
                    "agent.task_workers", 2
                ),
                key="AGENT_TASK_WORKERS", minimum=1, maximum=8,
            ),
            task_poll_seconds=_parse_float(
                _source_value(
                    merged_env, config, "AGENT_TASK_POLL_SECONDS",
                    "agent.task_poll_seconds", 5
                ),
                key="AGENT_TASK_POLL_SECONDS", minimum=0.5, maximum=60,
            ),
            worker_recovery_seconds=_parse_float(
                _source_value(
                    merged_env, config, "AGENT_WORKER_RECOVERY_SECONDS",
                    "agent.worker_recovery_seconds", 30
                ),
                key="AGENT_WORKER_RECOVERY_SECONDS", minimum=1, maximum=600,
            ),
        )

        workspace = WorkspaceSettings(
            root=Path(str(_source_value(
                merged_env, config, "GITHUB_WORKSPACE_ROOT",
                "workspace.root", "/home/ubuntu/github-workspaces"
            ))).expanduser(),
            allowed_repos=_csv(_source_value(
                merged_env, config, "GITHUB_ALLOWED_REPOS",
                "workspace.allowed_repos", ()
            )),
        )

        github = GitHubSettings(
            token=str(_source_value(
                merged_env, config, "GITHUB_TOKEN", "github.token", ""
            )).strip(),
            ci_workflow=(
                str(_source_value(
                    merged_env, config, "GITHUB_CI_WORKFLOW",
                    "github.ci_workflow", "CI"
                )).strip()
                or None
            ),
        )

        watchdog = WatchdogSettings(
            interval_seconds=_parse_int(
                _source_value(
                    merged_env, config, "WATCHDOG_INTERVAL_SECONDS",
                    "watchdog.interval_seconds", 300
                ),
                key="WATCHDOG_INTERVAL_SECONDS", minimum=60,
            ),
            stale_task_seconds=_parse_int(
                _source_value(
                    merged_env, config, "WATCHDOG_STALE_TASK_SECONDS",
                    "watchdog.stale_task_seconds", 7200
                ),
                key="WATCHDOG_STALE_TASK_SECONDS", minimum=300,
            ),
            restart_cooldown_seconds=_parse_int(
                _source_value(
                    merged_env, config, "WATCHDOG_RESTART_COOLDOWN_SECONDS",
                    "watchdog.restart_cooldown_seconds", 300
                ),
                key="WATCHDOG_RESTART_COOLDOWN_SECONDS", minimum=60,
            ),
            max_restarts_per_hour=_parse_int(
                _source_value(
                    merged_env, config, "WATCHDOG_MAX_RESTARTS_PER_HOUR",
                    "watchdog.max_restarts_per_hour", 3
                ),
                key="WATCHDOG_MAX_RESTARTS_PER_HOUR", minimum=1, maximum=10,
            ),
        )

        logging_settings = LoggingSettings(
            level=str(_source_value(
                merged_env, config, "LOG_LEVEL", "logging.level", "INFO"
            )).strip().upper() or "INFO",
        )

        features = FeatureFlags(
            auto_strongest_free_model=_parse_bool(
                _source_value(
                    merged_env, config, "AGENT_SCOUT_AUTO_STRONGEST",
                    "features.auto_strongest_free_model", True
                ),
                key="AGENT_SCOUT_AUTO_STRONGEST",
            ),
            adaptive_workers=_parse_bool(
                _source_value(
                    merged_env, config, "AGENT_ADAPTIVE_WORKERS",
                    "features.adaptive_workers", True
                ),
                key="AGENT_ADAPTIVE_WORKERS",
            ),
            scout_web_research=_parse_bool(
                _source_value(
                    merged_env, config, "AGENT_SCOUT_WEB_RESEARCH",
                    "features.scout_web_research", True
                ),
                key="AGENT_SCOUT_WEB_RESEARCH",
            ),
        )

        users = config.get("users", {})
        user_policies = {
            str(owner_id): dict(policy)
            for owner_id, policy in users.items()
            if isinstance(policy, Mapping)
        }
        settings = cls(
            telegram=telegram,
            opencode=opencode,
            agent=agent,
            workspace=workspace,
            github=github,
            watchdog=watchdog,
            logging=logging_settings,
            features=features,
            user_policies=user_policies,
            config_file=resolved_config_path,
        )
        settings.validate()
        for owner_id, policy in settings.user_policies.items():
            settings._validate_override(policy, source=f"users.{owner_id}")
        return settings

    @property
    def effective_pin_default_model(self) -> bool:
        return (
            self.opencode.pin_default_model
            and not self.features.auto_strongest_free_model
        )

    @property
    def effective_opencode_password(self) -> str | None:
        return self.opencode.password or self.opencode.server_password

    def validate(self) -> None:
        if not self.opencode.host:
            raise SettingsError("OPENCODE_HOST must not be empty")
        if not self.opencode.default_model:
            raise SettingsError("OPENCODE_DEFAULT_MODEL must not be empty")
        if self.telegram.attachment_max_total_bytes < self.telegram.attachment_max_bytes:
            raise SettingsError(
                "TELEGRAM_ATTACHMENT_MAX_TOTAL_BYTES must be >= "
                "TELEGRAM_ATTACHMENT_MAX_BYTES"
            )
        if self.logging.level not in {
            "CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"
        }:
            raise SettingsError("LOG_LEVEL is not a supported logging level")

    def require_bot_ready(self) -> None:
        if not self.telegram.bot_token:
            raise SettingsError("TELEGRAM_BOT_TOKEN is required")
        if not self.telegram.allowed_users:
            raise SettingsError(
                "TELEGRAM_ALLOWED_USERS must include at least one user"
            )

    _OVERRIDABLE: dict[str, tuple[str, type]] = field(
        default_factory=lambda: {
            "telegram.attachment_max_bytes": ("telegram", int),
            "telegram.attachment_max_count": ("telegram", int),
            "telegram.attachment_max_total_bytes": ("telegram", int),
            "telegram.attachment_pending_seconds": ("telegram", int),
            "opencode.default_model": ("opencode", str),
            "opencode.model_variant": ("opencode", str),
            "opencode.agent": ("opencode", str),
            "agent.task_workers": ("agent", int),
            "agent.task_poll_seconds": ("agent", float),
            "features.auto_strongest_free_model": ("features", bool),
            "features.adaptive_workers": ("features", bool),
            "features.scout_web_research": ("features", bool),
        },
        repr=False,
        compare=False,
    )

    def _flatten_override(
        self,
        override: Mapping[str, Any],
        prefix: str = "",
    ) -> dict[str, Any]:
        flattened: dict[str, Any] = {}
        for key, value in override.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                flattened.update(self._flatten_override(value, path))
            else:
                flattened[path] = value
        return flattened

    def _validate_override(
        self,
        override: Mapping[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        flat = self._flatten_override(override)
        unknown = set(flat) - set(self._OVERRIDABLE)
        if unknown:
            raise SettingsError(
                f"{source} contains non-overridable setting(s): "
                + ", ".join(sorted(unknown))
            )
        return flat

    def resolve_for(
        self,
        owner_id: str | None = None,
        *,
        task_override: Mapping[str, Any] | None = None,
    ) -> "BridgeSettings":
        resolved = self
        if owner_id is not None:
            policy = self.user_policies.get(str(owner_id))
            if policy:
                resolved = resolved._apply_override(
                    self._validate_override(policy, source=f"users.{owner_id}")
                )
        if task_override:
            resolved = resolved._apply_override(
                self._validate_override(task_override, source="task_override")
            )
        resolved.validate()
        return resolved

    def _apply_override(self, flat: Mapping[str, Any]) -> "BridgeSettings":
        sections: dict[str, dict[str, Any]] = {}
        for path, value in flat.items():
            section, field_name = path.split(".", 1)
            if path.startswith("features."):
                parsed = _parse_bool(value, key=path)
            elif path in {
                "telegram.attachment_max_bytes",
                "telegram.attachment_max_count",
                "telegram.attachment_max_total_bytes",
                "telegram.attachment_pending_seconds",
                "agent.task_workers",
            }:
                parsed = _parse_int(value, key=path, minimum=1)
            elif path == "agent.task_poll_seconds":
                parsed = _parse_float(value, key=path, minimum=0.5, maximum=60)
            else:
                parsed = str(value).strip()
            sections.setdefault(section, {})[field_name] = parsed

        result = self
        for section, changes in sections.items():
            current = getattr(result, section)
            result = replace(result, **{section: replace(current, **changes)})
        return result

    def public_dict(self, owner_id: str | None = None) -> dict[str, Any]:
        settings = self.resolve_for(owner_id)
        data = asdict(settings)
        data["telegram"]["bot_token"] = "<redacted>" if settings.telegram.bot_token else ""
        data["opencode"]["password"] = (
            "<redacted>" if settings.opencode.password else None
        )
        data["opencode"]["server_password"] = (
            "<redacted>" if settings.opencode.server_password else None
        )
        data["github"]["token"] = "<redacted>" if settings.github.token else ""
        data["workspace"]["root"] = str(settings.workspace.root)
        data["telegram"]["allowed_users"] = sorted(settings.telegram.allowed_users)
        data["telegram"]["allowed_chat_ids"] = sorted(settings.telegram.allowed_chat_ids)
        data["workspace"]["allowed_repos"] = list(settings.workspace.allowed_repos)
        data.pop("user_policies", None)
        data.pop("_OVERRIDABLE", None)
        data["config_file"] = (
            str(settings.config_file) if settings.config_file is not None else None
        )
        data["opencode"]["effective_pin_default_model"] = (
            settings.effective_pin_default_model
        )
        return data

    def limits_dict(self, owner_id: str | None = None) -> dict[str, Any]:
        settings = self.resolve_for(owner_id)
        return {
            "attachment_max_bytes": settings.telegram.attachment_max_bytes,
            "attachment_max_count": settings.telegram.attachment_max_count,
            "attachment_max_total_bytes": settings.telegram.attachment_max_total_bytes,
            "attachment_pending_seconds": settings.telegram.attachment_pending_seconds,
            "media_group_debounce_seconds": (
                settings.telegram.media_group_debounce_seconds
            ),
            "task_workers": settings.agent.task_workers,
            "task_poll_seconds": settings.agent.task_poll_seconds,
            "worker_recovery_seconds": settings.agent.worker_recovery_seconds,
        }


_SETTINGS: BridgeSettings | None = None
_SETTINGS_LOCK = Lock()


def get_settings(*, reload: bool = False) -> BridgeSettings:
    global _SETTINGS
    with _SETTINGS_LOCK:
        if reload or _SETTINGS is None:
            _SETTINGS = BridgeSettings.load(env_file=DEFAULT_ENV_FILE)
        return _SETTINGS
