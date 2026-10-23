"""Arabic Telegram bridge for a locally hosted OpenCode agent.

``bot.py`` is the **production orchestrator** (coordinator) of OpenCode Bridge.
It wires together the core bridge services and the V2 architecture layer under
``bridge/``:

- Settings and configuration (``bridge.config.get_settings``)
- Task queue and execution (``task_queue``, ``bridge.services.task_service``,
  ``bridge.services.task_execution_service``, ``worker_capacity``)
- OpenCode client (``opencode_client``)
- Session store (``bridge.domain.sessions.session_store``)
- Attachment store (``attachments``)
- Model manager, agent scout, adaptive workers, watchdog
- Telegram application, commands, callbacks, panels (``bridge.telegram.*``)

It is intentionally kept as a thin coordinator: business logic lives in the
``bridge/`` services and domain modules, while this module owns the
application lifecycle (``main``, ``post_init``, ``post_shutdown``), global
service instances, and the Telegram command/callback adapters that expose them.

Entry points:
- ``python -m run_v3`` — production entrypoint (used by systemd). Layers
  ``v3_plugin``, ``ci_plugin``, ``resource_commands``, ``watchdog_plugin`` and
  ``workspace_runtime`` on top of this module.
- ``python -m bot`` (via ``start.sh``) — manual/local entrypoint only.

Do not add new business logic here. Prefer adding it to the appropriate
``bridge/services`` or ``bridge/domain`` module and wiring it through an adapter
in this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import Application, ContextTypes
from telegram.request import HTTPXRequest

BRIDGE_DIR = Path(__file__).parent
MAINTENANCE_REPORT_PATH = BRIDGE_DIR / "runtime" / "maintenance-latest.md"
ATTACHMENT_ROOT = BRIDGE_DIR / "runtime" / "attachments"
sys.path.insert(0, str(BRIDGE_DIR))

from attachments import AttachmentStore
from audit_log import AuditLogger
from block_patterns import check_build, check_hardline
from bridge.config import get_settings
from bridge.config.runtime_flags import enabled as runtime_flag_enabled
from bridge.domain.policies import RequestGuard
from bridge.domain.sessions.session_store import SessionStore
from bridge.infrastructure.database.draft_store import DraftStore
from bridge.infrastructure.database.user_settings_store import UserSettingsStore
from bridge.infrastructure.opencode.credential_pool import CredentialPool, parse_credential_pool

# Automatic backup after every coherent development batch
BACKUP_DIR = BRIDGE_DIR / "runtime" / "backups"

class BackupAutoAdapter:
    def __init__(self) -> None:
        from bridge.services.backup_auto import BackupAutoManager
        self.manager = BackupAutoManager(
            source_paths=[
                BRIDGE_DIR / "sessions.db",
                BRIDGE_DIR / "runtime" / "audit.jsonl",
                BRIDGE_DIR / "runtime" / "skills-state.json",
            ],
            backup_dir=BACKUP_DIR,
            retention_days=3,
        )

    async def run(self) -> Path:
        zip_path = self.manager.create_zip()
        await self.manager.send_latest()
        self.manager.purge_older_than_days()
        return zip_path

backup_adapter = BackupAutoAdapter()

from bridge.services.agent_service import AgentService
from bridge.services.config_service import ConfigurationService
from bridge.services.draft_service import DraftService
from bridge.services.host_credentials import HostCredentialManager
from bridge.services.maintenance_service import MaintenanceReportService
from bridge.services.media_service import MediaTaskService
from bridge.services.metrics_service import BridgeMetricsService
from bridge.services.model_selection_service import ModelSelectionService
from bridge.services.provider_keys import ProviderKeyManager
from bridge.services.runtime_controls import RuntimeControls
from bridge.services.schedule_service import ScheduleService
from bridge.services.skill_sync import DEFAULT_SOURCE as SKILLS_SOURCE_DEFAULT
from bridge.services.skill_sync import SkillSyncService
from bridge.services.task_execution_service import TaskExecutionService
from bridge.services.task_service import TaskApplicationService
from bridge.services.user_preferences_service import UserPreferencesService
from bridge.services.worker_capacity import WorkerCapacity
from bridge.telegram.app import build_application, core_commands, register_core_handlers
from bridge.telegram.attachments import TelegramMediaAdapter
from bridge.telegram.callbacks.interactions import AgentInteractionCallbacks
from bridge.telegram.callbacks.model_picker import ModelPickerCallbackAdapter
from bridge.telegram.callbacks.reboot import RebootCallbackAdapter
from bridge.telegram.callbacks.schedules import ScheduleCallbacks
from bridge.telegram.commands.agent import AgentCommands
from bridge.telegram.commands.config import ConfigCommands
from bridge.telegram.commands.downloads import DownloadCommands
from bridge.telegram.commands.drafts import DraftCommands
from bridge.telegram.commands.manage import ManageCommands
from bridge.telegram.commands.metrics import MetricsCommands
from bridge.telegram.commands.schedules import ScheduleCommands
from bridge.telegram.commands.shell import ShellCommands
from bridge.telegram.commands.skills import SkillCommands
from bridge.telegram.commands.system import SystemCommands
from bridge.telegram.commands.tasks import TaskCommands
from bridge.telegram.control_ui import ControlUI
from bridge.telegram.draft_intake import DraftIntakeAdapter
from bridge.telegram.execution import TelegramExecutionDelivery
from bridge.telegram.helpers import parse_utc_datetime, task_status_text
from bridge.telegram.interactions import InteractionRegistry
from bridge.telegram.middleware import TelegramAccessController
from bridge.telegram.panels import (
    ACT,
    PanelContext,
    PanelRouter,
    build_panels,
)
from bridge.telegram.role_policy import resolve_owner_id
from formatter import MAX_MESSAGE_LENGTH
from messages import (
    HELP_TEXT,
    startup_message,
    unauthorized_message,
    user_error,
)
from model_manager import ModelManager
from opencode_client import OpenCodeClient
from progress import ProgressStore
from progress_reporter import DisplayPreferences, LiveProgressReporter, register_interaction_sink
from prompt_enhancer import ResearchMode
from reboot_state import decision_path, read_state, request_path, write_state
from task_queue import QueuedTask, TaskQueueStore
from task_service import TaskService

if TYPE_CHECKING:
    from bridge.services.admin_backups import AdminBackups
    from bridge.services.download_service import DownloadService
    from bridge.services.github.commands import GitHubCommandHandler
    from bridge.services.host_control import HostControl
    from bridge.services.mirrors import MirrorService

REBOOT_REQUEST_PATH = request_path(BRIDGE_DIR / "runtime")
REBOOT_DECISION_PATH = decision_path(BRIDGE_DIR / "runtime")

SETTINGS = get_settings()
SETTINGS.require_bot_ready()

logging.basicConfig(
    level=SETTINGS.logging.level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)
log = logging.getLogger("opencode_bridge")

TELEGRAM_BOT_TOKEN = SETTINGS.telegram.bot_token
TELEGRAM_PROXY_URL = SETTINGS.telegram.proxy_url
ALLOWED_USERS = set(SETTINGS.telegram.allowed_users)
CONTROL_OWNER_ID = resolve_owner_id(ALLOWED_USERS, SETTINGS.telegram.owner_id)
ALLOWED_CHAT_IDS = set(SETTINGS.telegram.allowed_chat_ids)
OPENCODE_HOST = SETTINGS.opencode.host
OPENCODE_PORT = SETTINGS.opencode.port
OPENCODE_PASSWORD = SETTINGS.opencode.password
OPENCODE_CREDENTIALS = SETTINGS.opencode.credentials
DEFAULT_MODEL = SETTINGS.opencode.default_model
DEFAULT_MODEL_VARIANT = SETTINGS.opencode.model_variant
VARIANT_MODEL = SETTINGS.opencode.variant_model
AUTO_STRONGEST_FREE_MODEL = SETTINGS.features.auto_strongest_free_model
PIN_DEFAULT_MODEL = SETTINGS.effective_pin_default_model
DEFAULT_AGENT = SETTINGS.opencode.agent
MODEL_CATALOG_SYNC_SECONDS = SETTINGS.opencode.model_sync_seconds
# How often the agent's skills are refreshed from their repository. Six hours is
# frequent enough that a pushed skill is live the same afternoon, and rare enough
# that a scheduled task is never competing with a research run for the network.
SKILLS_SYNC_SECONDS = max(
    900.0,
    float(os.environ.get("BRIDGE_SKILLS_SYNC_MINUTES", "360") or 360) * 60.0,
)
ATTACHMENT_MAX_BYTES = SETTINGS.telegram.attachment_max_bytes
ATTACHMENT_MAX_COUNT = SETTINGS.telegram.attachment_max_count
ATTACHMENT_MAX_TOTAL_BYTES = SETTINGS.telegram.attachment_max_total_bytes
ATTACHMENT_PENDING_SECONDS = SETTINGS.telegram.attachment_pending_seconds
MEDIA_GROUP_DEBOUNCE_SECONDS = SETTINGS.telegram.media_group_debounce_seconds
DROP_PENDING_UPDATES = SETTINGS.telegram.drop_pending_updates

store = SessionStore(BRIDGE_DIR / "sessions.db")
credential_pool = CredentialPool(parse_credential_pool(OPENCODE_CREDENTIALS))
credential_state_path = BRIDGE_DIR / "runtime" / "opencode-credential-state.json"
try:
    credential_pool.load_state(credential_state_path)
except (OSError, ValueError, json.JSONDecodeError):
    log.warning("تعذر استعادة حالة OpenCode credentials؛ سيبدأ الـpool بحالة نظيفة")
task_store = TaskQueueStore(BRIDGE_DIR / "sessions.db")
client = OpenCodeClient(
    host=OPENCODE_HOST,
    port=OPENCODE_PORT,
    password=OPENCODE_PASSWORD,
    credential_pool=credential_pool,
    credential_state_path=credential_state_path,
)
provider_keys = ProviderKeyManager(BRIDGE_DIR / "runtime" / "private" / "provider-keys.json", client)
audit = AuditLogger(BRIDGE_DIR / "runtime" / "audit.jsonl")
attachment_store = AttachmentStore(
    ATTACHMENT_ROOT,
    max_bytes=ATTACHMENT_MAX_BYTES,
    max_count=ATTACHMENT_MAX_COUNT,
    max_total_bytes=ATTACHMENT_MAX_TOTAL_BYTES,
    # A local Bot API server keeps uploads private to its own service user; this
    # endpoint is how the bridge reaches those files without filesystem access.
    local_file_base_url=SETTINGS.telegram.local_file_base_url,
)
progress_store = ProgressStore()
live_reporters: dict[int, LiveProgressReporter] = {}
task_service: TaskService | None = None
model_manager: ModelManager | None = None
github_handler: GitHubCommandHandler | None = None
pending_cleanup_task: asyncio.Task[None] | None = None
_media_adapter_instance: TelegramMediaAdapter | None = None
_task_commands_instance: TaskCommands | None = None
_schedule_commands_instance: ScheduleCommands | None = None
_agent_commands_instance: AgentCommands | None = None
_system_commands_instance: SystemCommands | None = None
_config_commands_instance: ConfigCommands | None = None
_metrics_commands_instance: MetricsCommands | None = None
_admin_backups_instance: AdminBackups | None = None
_host_control_instance: HostControl | None = None


# ---------------------------------------------------------------------------
# GitHub command shims
#
# ``GitHubCommandHandler`` carries the real implementation; these thin module-level
# shims let ``bridge.telegram.app.register_core_handlers`` wire the GitHub commands
# into the application's command registry so they stay aligned with ``core_commands()``.
# Each shim is a single delegated ``await`` and is safe when no GitHub token is
# configured (``github_handler`` is then ``None``).
# ---------------------------------------------------------------------------

async def _github(update: Any, context: Any, method: str) -> None:
    handler = github_handler
    if handler is None:
        return
    target = getattr(handler, method, None)
    if target is None:
        return
    await target(update, context)


async def cmd_ghrepos(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_repos")


async def cmd_ghcreate(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_create")


async def cmd_ghclone(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_clone")


async def cmd_ghstatus(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_status")


async def cmd_ghpr(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_pr")


async def cmd_ghissue(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_issue")


async def cmd_ghrelease(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_release")


async def cmd_ghbranch(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_branch")


async def cmd_ghsecret(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_secret")


async def cmd_ghhelp(update: Any, context: Any) -> None:
    await _github(update, context, "cmd_github_help")
_draft_commands_instance: DraftCommands | None = None
_draft_intake_instance: DraftIntakeAdapter | None = None
_reboot_callback_instance: RebootCallbackAdapter | None = None
_model_picker_callback_instance: ModelPickerCallbackAdapter | None = None
_interaction_callbacks_instance: AgentInteractionCallbacks | None = None
_interaction_registry_instance: InteractionRegistry | None = None
_skill_sync_instance: SkillSyncService | None = None
_skill_commands_instance: SkillCommands | None = None
skill_sync_task: asyncio.Task[None] | None = None
_model_selection_service: ModelSelectionService | None = None
_user_preferences_service: UserPreferencesService | None = None
_panel_router_instance: PanelRouter | None = None
_control_ui_instance: ControlUI | None = None
_download_service: DownloadService | None = None
_download_commands_instance: DownloadCommands | None = None
_manage_commands_instance: ManageCommands | None = None
_shell_commands_instance: ShellCommands | None = None
_mirror_service_instance: MirrorService | None = None
download_cleanup_task: asyncio.Task[None] | None = None
_access_controller_instance: TelegramAccessController | None = None
_request_guard = RequestGuard(()) if runtime_flag_enabled("OPENCODE_FULL_ACCESS") else RequestGuard((check_build, check_hardline))
_agent_service = AgentService(
    client,
    store,
    default_model=DEFAULT_MODEL,
    default_agent=DEFAULT_AGENT,
    default_variant=DEFAULT_MODEL_VARIANT,
    variant_model=VARIANT_MODEL,
    model_manager_provider=lambda: model_manager,
    logger=log,
    agent_preference_provider=lambda owner_id: _user_preferences().get(owner_id),
    provider_keys=provider_keys,
)
_task_application_service = TaskApplicationService(
    task_store,
    _agent_service,
    attachment_store,
    _request_guard,
)
_media_service = MediaTaskService(
    task_store,
    attachment_store,
    _request_guard,
    pending_ttl_seconds=ATTACHMENT_PENDING_SECONDS,
    max_count=ATTACHMENT_MAX_COUNT,
    max_total_bytes=ATTACHMENT_MAX_TOTAL_BYTES,
)
_schedule_service = ScheduleService(task_store, _request_guard)
_draft_service = DraftService(DraftStore(task_store.database), _task_application_service, _schedule_service)
_task_execution_service = TaskExecutionService(
    task_store,
    _agent_service,
    attachment_store,
    audit_write=audit.write,
    logger=log,
)
_maintenance_service = MaintenanceReportService(MAINTENANCE_REPORT_PATH)
_configuration_service = ConfigurationService(SETTINGS)
UTC = UTC

F = TypeVar("F", bound=Callable[..., Awaitable[None]])

RESEARCH_COMMAND_MODES: dict[str, ResearchMode] = {
    "search": ResearchMode.SEARCH,
    "deepresearch": ResearchMode.DEEP_RESEARCH,
    "extreme": ResearchMode.EXTREME,
    "news": ResearchMode.NEWS,
    "compare": ResearchMode.COMPARE,
    "factcheck": ResearchMode.FACT_CHECK,
    "verify": ResearchMode.VERIFY,
    "open": ResearchMode.OPEN,
    "extract": ResearchMode.EXTRACT,
}
RESEARCH_COMMAND_LABELS: dict[ResearchMode, str] = {
    ResearchMode.SEARCH: "بحث موثّق",
    ResearchMode.DEEP_RESEARCH: "بحث عميق",
    ResearchMode.EXTREME: "بحث شديد العمق",
    ResearchMode.NEWS: "بحث إخباري",
    ResearchMode.COMPARE: "مقارنة موثّقة",
    ResearchMode.FACT_CHECK: "تدقيق ادعاء",
    ResearchMode.VERIFY: "تحقق محدد",
    ResearchMode.OPEN: "فحص مصدر أو رابط",
    ResearchMode.EXTRACT: "استخراج بيانات",
}


def _access_controller() -> TelegramAccessController:
    global _access_controller_instance
    if _access_controller_instance is None:
        _access_controller_instance = TelegramAccessController(
            ALLOWED_USERS,
            ALLOWED_CHAT_IDS,
            reply=_safe_reply,
            unauthorized_text=unauthorized_message,
            audit_write=audit.write,
            logger=log,
        )
    return _access_controller_instance


def _is_allowed(update: Update) -> bool:
    """Compatibility wrapper for Telegram middleware."""
    return _access_controller().is_allowed(update)


def _is_control_owner(update: Update) -> bool:
    """Only a validated owner in a private chat may operate the host."""
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    actor = getattr(user, "id", None)
    return bool(
        CONTROL_OWNER_ID is not None and type(actor) is int
        and actor == CONTROL_OWNER_ID
        and getattr(chat, "type", None) == "private"
        and _is_allowed(update)
    )


def _reboot_callback_adapter() -> RebootCallbackAdapter:
    global _reboot_callback_instance
    if _reboot_callback_instance is None:
        _reboot_callback_instance = RebootCallbackAdapter(
            is_allowed=_is_control_owner,
            request_path=REBOOT_REQUEST_PATH,
            decision_path=REBOOT_DECISION_PATH,
            read_state=read_state,
            write_state=write_state,
            audit_write=audit.write,
        )
    return _reboot_callback_instance


def _user_preferences() -> UserPreferencesService:
    global _user_preferences_service
    if _user_preferences_service is None:
        _user_preferences_service = UserPreferencesService(UserSettingsStore(task_store.database))
    return _user_preferences_service


async def _display_preferences(owner_id: str) -> DisplayPreferences:
    """Read the owner's live display choices; fall back to defaults on any error."""
    try:
        preferences = await _user_preferences().get(owner_id)
    except Exception as exc:
        log.info("تعذر قراءة تفضيلات العرض للمالك %s: %s", owner_id, type(exc).__name__)
        return DisplayPreferences()
    return DisplayPreferences(
        notification_level=preferences.notification_level,
        output_style=preferences.output_style,
    )


async def _apply_model_to_owner_session(owner_id: str, session_id: str, model_id: str) -> None:
    await client.update_session(session_id, model=model_id)
    await store.update_session(owner_id, model=model_id)


async def _owner_variant_choice(owner_id: str, model_id: str) -> str | None:
    """Return the owner's explicit reasoning level, validated against the live catalog."""
    preferences = await _user_preferences().get(owner_id)
    if not preferences.model_pinned or preferences.model_preference != model_id:
        return None
    if not preferences.model_variant:
        return None
    providers = await client.list_providers()
    from model_catalog import model_variant_ids

    available = {value.casefold() for value in model_variant_ids(providers, model_id)}
    return preferences.model_variant if preferences.model_variant.casefold() in available else None


def _model_selection() -> ModelSelectionService:
    global _model_selection_service
    if _model_selection_service is None:
        async def variant_resolver(owner_id: str, model_id: str) -> str | None:
            if model_manager is None:
                return None
            return await model_manager.resolve_variant(owner_id, model_id)

        _model_selection_service = ModelSelectionService(
            catalog_provider=client.list_providers,
            preferences=_user_preferences(),
            session_model_reader=store.get_session,
            session_model_writer=_apply_model_to_owner_session,
            variant_resolver=variant_resolver,
            audit_write=audit.write,
        )
    return _model_selection_service


def _model_picker_callbacks() -> ModelPickerCallbackAdapter:
    global _model_picker_callback_instance
    if _model_picker_callback_instance is None:
        _model_picker_callback_instance = ModelPickerCallbackAdapter(
            service=_model_selection(),
            is_allowed=_is_allowed,
            answer=lambda query, text=None, show_alert=False: query.answer(text, show_alert=show_alert),
            logger=log,
        )
    return _model_picker_callback_instance


def _interaction_callbacks() -> AgentInteractionCallbacks:
    """The second direction of the conversation: the agent asks, Telegram answers."""
    global _interaction_callbacks_instance
    if _interaction_callbacks_instance is None:
        _interaction_callbacks_instance = AgentInteractionCallbacks(
            client=client,
            registry=_interaction_registry(),
            is_allowed=_is_allowed,
            audit_write=audit.write,
            logger=log,
        )
        register_interaction_sink(_interaction_callbacks_instance)
    return _interaction_callbacks_instance


def _interaction_registry() -> InteractionRegistry:
    global _interaction_registry_instance
    if _interaction_registry_instance is None:
        _interaction_registry_instance = InteractionRegistry()
    return _interaction_registry_instance


def _downloads() -> DownloadService:
    global _download_service
    if _download_service is None:
        # Imported lazily: this module costs ~150 ms at startup and most
        # launches never open a download command before the first restart.
        from bridge.services.download_service import DownloadService

        _download_service = DownloadService(
            BRIDGE_DIR / "runtime" / "downloads",
            max_file_bytes=SETTINGS.downloads.max_file_bytes,
            max_total_bytes=SETTINGS.downloads.max_total_bytes,
            ttl_hours=SETTINGS.downloads.ttl_hours,
            direct_send_limit=SETTINGS.downloads.direct_send_limit,
        )
    return _download_service


def _download_command_adapter() -> DownloadCommands:
    global _download_commands_instance
    if _download_commands_instance is None:
        _download_commands_instance = DownloadCommands(
            _downloads(),
            reply=_safe_reply,
            error_message=user_error,
            audit_write=audit.write,
            logger=log,
        )
    return _download_commands_instance


def _mirrors() -> MirrorService:
    global _mirror_service_instance
    if _mirror_service_instance is None:
        from bridge.services.mirrors import MirrorService

        _mirror_service_instance = MirrorService(
            BRIDGE_DIR / "runtime" / "downloads",
            extra_roots=(ATTACHMENT_ROOT / "outgoing",),
        )
    return _mirror_service_instance


def _manage_command_adapter() -> ManageCommands:
    global _manage_commands_instance
    if _manage_commands_instance is None:
        _manage_commands_instance = ManageCommands(
            _task_application_service,
            _schedule_service,
            _downloads(),
            _mirrors(),
            attachment_store,
            reply=_safe_reply,
            create_status=_create_task_status_message,
            edit_status=_edit_task_status_message,
            wake_tasks=_wake_task_workers,
            error_message=user_error,
            audit_write=audit.write,
            logger=log,
        )
    return _manage_commands_instance


def _shell_command_adapter() -> ShellCommands:
    global _shell_commands_instance
    if _shell_commands_instance is None:
        _shell_commands_instance = ShellCommands(
            reply=_safe_reply,
            error_message=user_error,
            audit_write=audit.write,
            is_owner=_is_control_owner,
            project_root=BRIDGE_DIR,
            logger=log,
        )
    return _shell_commands_instance


def _download_cleanup_loop() -> None:
    """Purge prior UTC-day cache only if all queued work can tolerate it."""
    try:
        from bridge.services.cache_retention import midnight_cleanup

        result = midnight_cleanup(BRIDGE_DIR, datetime.now(UTC))
        if result.get("status") == "completed" and result["removed"]:
            log.info(
                "تنظيف التنزيلات: حُذف %s ملف (%s بايت)",
                result["removed"],
                result["freed_bytes"],
            )
        elif result.get("status") == "skipped":
            log.info("تأجيل تنظيف التنزيلات: %s", result.get("reason", "safety gate"))
    except Exception as exc:
        log.info("تعذر تنظيف التنزيلات: %s", type(exc).__name__)


async def _download_cleanup_task() -> None:
    """Expire files at 00:00 UTC only, after at least 24 hours of retention."""
    from datetime import timedelta
    while True:
        now = datetime.now(UTC)
        tomorrow = (now + timedelta(days=1)).date()
        next_midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC)
        await asyncio.sleep(max(1, (next_midnight - now).total_seconds()))
        _download_cleanup_loop()


def _schedule_download_cleanup() -> None:
    global download_cleanup_task
    if download_cleanup_task is None or download_cleanup_task.done():
        download_cleanup_task = asyncio.create_task(
            _download_cleanup_task(), name="opencode-bridge-download-cleanup"
        )


def _panel_providers() -> dict[str, Any]:
    """Real data sources for the control panel. Nothing here is mocked state."""

    async def health(owner_id: str) -> Any:
        del owner_id
        from bridge.services.health_service import ComponentHealth, HealthService, HealthState

        checks: dict[str, ComponentHealth] = {}

        try:
            payload = await client.health()
            checks["opencode"] = ComponentHealth(
                "opencode",
                HealthState.HEALTHY if payload.get("healthy") else HealthState.UNHEALTHY,
                str(payload.get("version", "")),
            )
        except Exception as exc:
            checks["opencode"] = ComponentHealth("opencode", HealthState.UNHEALTHY, type(exc).__name__)

        try:
            await store.get_session("0")
            checks["db"] = ComponentHealth("db", HealthState.HEALTHY, BRIDGE_DIR.name)
        except Exception as exc:
            checks["db"] = ComponentHealth("db", HealthState.UNHEALTHY, type(exc).__name__)

        try:
            free = shutil.disk_usage(BRIDGE_DIR).free
            total = shutil.disk_usage(BRIDGE_DIR).total
            percent = round(free * 100 / total, 1) if total else 0.0
            checks["disk"] = ComponentHealth(
                "disk",
                HealthState.HEALTHY if percent >= 10 else HealthState.DEGRADED,
                f"{percent}% free",
            )
        except Exception as exc:
            checks["disk"] = ComponentHealth("disk", HealthState.UNHEALTHY, type(exc).__name__)

        workers = SETTINGS.agent.task_workers
        checks["workers"] = ComponentHealth(
            "workers", HealthState.HEALTHY, f"max {workers}"
        )

        catalog = getattr(model_manager, "_providers_cache", None) if model_manager else None
        checks["model_catalog"] = ComponentHealth(
            "model_catalog",
            HealthState.HEALTHY if catalog else HealthState.DEGRADED,
            "loaded" if catalog else "not cached yet",
        )

        checks["telegram"] = ComponentHealth("telegram", HealthState.HEALTHY, "polling")
        return HealthService().health(checks)

    async def commands(owner_id: str) -> tuple[str, ...]:
        del owner_id
        return tuple(sorted(item.command for item in core_commands()))

    async def limits(owner_id: str) -> dict[str, Any]:
        return _configuration_service.effective_limits(owner_id)

    async def config(owner_id: str) -> dict[str, Any]:
        return _configuration_service.public_config(owner_id)

    return {
        "health": health,
        "commands": commands,
        "limits": limits,
        "config": config,
        "preferences": lambda owner_id: _user_preferences().get(owner_id),
        "active": lambda owner_id: _task_application_service.active(owner_id),
        "failed": lambda owner_id: _task_application_service.failed(owner_id),
        "schedules": lambda owner_id: task_store.list_scheduled_jobs(owner_id),
        "downloads": lambda owner_id: _downloads().list(owner_id),
        "capabilities": lambda owner_id: _downloads().capabilities(),
    }


def _panel_actions() -> dict[tuple[str, str], Any]:
    async def noop(context: PanelContext, arg: str) -> None:
        return None

    async def cancel_task(context: PanelContext, arg: str) -> None:
        await _task_application_service.cancel(int(arg), context.owner_id)

    async def retry_task(context: PanelContext, arg: str) -> None:
        await _task_application_service.retry_failed(context.owner_id, int(arg))

    async def toggle_schedule(context: PanelContext, arg: str) -> None:
        job = await task_store.get_scheduled_job_by_id(int(arg), context.owner_id)
        if job is not None:
            await task_store.set_scheduled_job_enabled(context.owner_id, job.name, not job.enabled)

    async def delete_schedule(context: PanelContext, arg: str) -> None:
        job = await task_store.get_scheduled_job_by_id(int(arg), context.owner_id)
        if job is not None:
            await task_store.delete_scheduled_job(context.owner_id, job.name)

    async def set_preference(context: PanelContext, arg: str) -> None:
        field, _, raw = arg.partition("=")
        if field not in _EDITABLE_PREFERENCES:
            raise ValueError(f"preference is not editable: {field}")
        value: object = raw
        if field == "retention_days":
            days = int(raw)
            if days not in (1, 7, 30, 90):
                raise ValueError("unsupported retention window")
            value = days
        preferences = await _user_preferences().update(context.owner_id, **{field: value})
        _write_retention_window(preferences.retention_days)
        log.info(
            "تم تحديث تفضيل %s للمالك %s", field, context.owner_id
        )

    async def delete_download(context: PanelContext, arg: str) -> None:
        _downloads().delete(context.owner_id, arg)

    return {
        ("system", "refresh"): noop,
        ("tasks", "refresh"): noop,
        ("schedules", "refresh"): noop,
        ("downloads", "refresh"): noop,
        ("tasks", "cancel"): cancel_task,
        ("tasks", "retry"): retry_task,
        ("schedules", "toggle_schedule"): toggle_schedule,
        ("schedules", "delete_schedule"): delete_schedule,
        ("downloads", "delete_download"): delete_download,
        ("settings.notification", ACT): set_preference,
        ("settings.output", ACT): set_preference,
        ("settings.retention", ACT): set_preference,
    }


_EDITABLE_PREFERENCES = frozenset({"notification_level", "output_style", "retention_days"})
RETENTION_FILE = BRIDGE_DIR / "runtime" / "retention-days"


def _write_retention_window(days: int) -> None:
    """Publish the owner's retention window for the root maintenance job."""
    try:
        RETENTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = RETENTION_FILE.with_suffix(".tmp")
        temporary.write_text(f"{int(days)}\n", encoding="utf-8")
        os.replace(temporary, RETENTION_FILE)
        os.chmod(RETENTION_FILE, 0o644)
    except OSError as exc:
        log.warning("تعذر نشر مدة الاحتفاظ: %s", exc)


def _panel_router() -> PanelRouter:
    global _panel_router_instance
    if _panel_router_instance is None:
        providers = _panel_providers()

        def build_context(owner_id: str) -> PanelContext:
            bound = {key: (lambda owner=owner_id, value=value: value(owner)) for key, value in providers.items()}
            return PanelContext(owner_id, bound)

        _panel_router_instance = PanelRouter(
            panels=build_panels(),
            providers=build_context,
            actions=_panel_actions(),
            is_allowed=_is_allowed,
            logger=log,
        )
    return _panel_router_instance


async def _dispatch_advanced_button(name: str, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    payload: str) -> None:
    """Dispatch an allowlisted button command through the existing authorized adapter."""
    if name not in frozenset(["new","abort","share","unshare","status","health","agents","model","tasks","failed","progress","trace","cancel","retry","discard","schedules","schedule","repeat","schedshow","schedhistory","schedrename","schededit","schedappend","schedtime","schedinterval","schedtimezone","schedrun","schedpause","schedresume","scheddelete","draft","config","limits","metrics","maintenance","download","deletefile","mirror","prompt","topics","taskshow","dailies","exec"]):
        raise ValueError("Unsupported advanced action")
    handler = globals().get("cmd_" + name)
    if handler is None:
        raise ValueError("Advanced command handler unavailable")
    previous = context.args
    if update.message is None and update.effective_message is not None:
        from types import SimpleNamespace
        update = SimpleNamespace(
            message=update.effective_message, effective_message=update.effective_message,
            effective_user=update.effective_user, effective_chat=update.effective_chat,
            effective_bot=update.effective_bot, update_id=update.update_id, get_bot=update.get_bot,
        )
    try:
        context.args = payload.split()
        await handler(update, context)
    finally:
        context.args = previous


def _admin_backups() -> AdminBackups:
    """Construct the backup service only when the control panel needs it."""
    global _admin_backups_instance
    if _admin_backups_instance is None:
        from bridge.services.admin_backups import AdminBackups

        _admin_backups_instance = AdminBackups(BRIDGE_DIR)
    return _admin_backups_instance


def _host_control() -> HostControl:
    """Construct host controls lazily; importing them costs startup time."""
    global _host_control_instance
    if _host_control_instance is None:
        from bridge.services.host_control import HostControl

        _host_control_instance = HostControl(BRIDGE_DIR)
    return _host_control_instance


def _control_ui() -> ControlUI:
    global _control_ui_instance
    if _control_ui_instance is None:
        _control_ui_instance = ControlUI(
            preferences=_user_preferences(),
            agents=_agent_service,
            models=_model_selection(),
            tasks=_task_application_service,
            schedule_store=task_store,
            schedule_service=_schedule_service,
            downloads=_downloads(),
            capacity=WorkerCapacity(BRIDGE_DIR / "runtime" / "worker-capacity"),
            wake=_wake_task_workers,
            allowed=_is_allowed,
            provider_keys=provider_keys,
            runtime_controls=RuntimeControls(BRIDGE_DIR / "runtime"),
            host_credentials=HostCredentialManager(BRIDGE_DIR),
            backups=_admin_backups(),
            mirrors=_mirrors(),
            project=BRIDGE_DIR,
            admin_allowed=_is_control_owner,
            legacy_dispatch=_dispatch_advanced_button,
            research_modes=RESEARCH_COMMAND_MODES,
            host_control=_host_control(),
            audit=audit.write,
        )
    return _control_ui_instance


def _wake_task_workers() -> None:
    if task_service is None:
        raise RuntimeError("خدمة المهام غير مهيأة بعد")
    task_service.wake()


def _media_adapter() -> TelegramMediaAdapter:
    global _media_adapter_instance
    if _media_adapter_instance is None:
        _media_adapter_instance = TelegramMediaAdapter(
            attachment_store,
            _media_service,
            reply=_safe_reply,
            create_status=_create_task_status_message,
            edit_status=_edit_task_status_message,
            wake_tasks=_wake_task_workers,
            error_message=user_error,
            audit_write=audit.write,
            debounce_seconds=MEDIA_GROUP_DEBOUNCE_SECONDS,
            logger=log,
        )
    return _media_adapter_instance


def _schedule_command_adapter() -> ScheduleCommands:
    global _schedule_commands_instance
    if _schedule_commands_instance is None:
        _schedule_commands_instance = ScheduleCommands(
            _schedule_service,
            reply=_safe_reply,
            create_status=_create_task_status_message,
            edit_status=_edit_task_status_message,
            wake_tasks=_wake_task_workers,
            error_message=user_error,
            audit_write=audit.write,
            max_message_length=MAX_MESSAGE_LENGTH,
            logger=log,
        )
    return _schedule_commands_instance


_schedule_callbacks_instance = None

def _schedule_callback_adapter() -> ScheduleCallbacks:
    global _schedule_callbacks_instance
    if _schedule_callbacks_instance is None:
        _schedule_callbacks_instance = ScheduleCallbacks(_schedule_service, max_message_length=MAX_MESSAGE_LENGTH)
    return _schedule_callbacks_instance

async def handle_schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_callback_adapter().handle(update, context)


def _draft_command_adapter() -> DraftCommands:
    global _draft_commands_instance
    if _draft_commands_instance is None:
        _draft_commands_instance = DraftCommands(_draft_service, _safe_reply, _wake_task_workers)
    return _draft_commands_instance

async def cmd_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _draft_command_adapter().handle(update, context)


def _draft_intake_adapter() -> DraftIntakeAdapter:
    global _draft_intake_instance
    if _draft_intake_instance is None:
        _draft_intake_instance = DraftIntakeAdapter(
            _draft_service, _media_adapter(), attachment_store, _safe_reply, _task_command_adapter(),
            control_ui=lambda: _control_ui(),
        )
    return _draft_intake_instance

def _task_command_adapter() -> TaskCommands:
    global _task_commands_instance
    if _task_commands_instance is None:
        _task_commands_instance = TaskCommands(
            _task_application_service,
            _schedule_service,
            _media_adapter,
            reply=_safe_reply,
            create_status=_create_task_status_message,
            edit_status=_edit_task_status_message,
            wake_tasks=_wake_task_workers,
            error_message=user_error,
            audit_write=audit.write,
            live_reporters=live_reporters,
            progress_store=progress_store,
            max_message_length=MAX_MESSAGE_LENGTH,
            research_modes=RESEARCH_COMMAND_MODES,
            logger=log,
        )
    return _task_commands_instance


def _agent_command_adapter() -> AgentCommands:
    global _agent_commands_instance
    if _agent_commands_instance is None:
        _agent_commands_instance = AgentCommands(
            _agent_service,
            reply=_safe_reply,
            error_message=user_error,
            startup_text=startup_message,
            on_start_ui=lambda update, context: _control_ui().show(update, context, persistent=True),
            selection_service=_model_selection(),
            picker_callbacks=_model_picker_callbacks(),
            logger=log,
        )
    return _agent_commands_instance


def _skill_sync() -> SkillSyncService:
    global _skill_sync_instance
    if _skill_sync_instance is None:
        source = os.environ.get("BRIDGE_SKILLS_SOURCE") or os.environ.get(
            "OPENCODE_SKILLS_SOURCE", SKILLS_SOURCE_DEFAULT,
        )
        _skill_sync_instance = SkillSyncService(
            BRIDGE_DIR / "runtime",
            source=source,
            logger=log,
        )
    return _skill_sync_instance


def _skill_command_adapter() -> SkillCommands:
    global _skill_commands_instance
    if _skill_commands_instance is None:
        _skill_commands_instance = SkillCommands(
            _skill_sync(),
            reply=_safe_reply,
            reply_html=_safe_html_reply,
            error_message=user_error,
            reload_service=_skill_sync().reload_agent_server,
            audit_write=audit.write,
            logger=log,
        )
    return _skill_commands_instance


async def _skill_sync_loop() -> None:
    """Keep the agent's skills current without the owner ever asking."""
    interval = SKILLS_SYNC_SECONDS
    while True:
        try:
            await asyncio.sleep(interval)
            if task_store is None:
                continue
            await _skill_command_adapter().sync_quietly()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a scheduler must never die on one bad fetch
            log.info("توقفت مزامنة المهارات التلقائية: %s", type(exc).__name__)


def _system_command_adapter() -> SystemCommands:
    global _system_commands_instance
    if _system_commands_instance is None:
        _system_commands_instance = SystemCommands(
            _maintenance_service,
            reply=_safe_reply,
            help_text=HELP_TEXT,
            error_message=user_error,
            logger=log,
        )
    return _system_commands_instance


def _config_command_adapter() -> ConfigCommands:
    global _config_commands_instance
    if _config_commands_instance is None:
        async def _zen_pause() -> Any:
            try:
                return await task_store.get_provider_quota_pause("opencode_zen_free")
            except Exception:
                return None

        _config_commands_instance = ConfigCommands(
            _configuration_service,
            reply=_safe_reply,
            quota_pause_reader=_zen_pause,
        )
    return _config_commands_instance


def _metrics_command_adapter() -> MetricsCommands:
    global _metrics_commands_instance
    if _metrics_commands_instance is None:
        def _worker_limit() -> int:
            """Current admission ceiling; 0 before the workers exist."""
            service = task_service
            if service is None:
                return 0
            try:
                return int(service.active_worker_limit())
            except Exception:
                return 0

        _metrics_commands_instance = MetricsCommands(
            BridgeMetricsService(
                task_store,
                audit_path=BRIDGE_DIR / "runtime" / "audit.jsonl",
                worker_limit=_worker_limit,
                disk_path=BRIDGE_DIR,
            ),
            reply=_safe_reply,
            error_message=user_error,
        )
    return _metrics_commands_instance


async def _pending_attachment_cleanup_loop() -> None:
    await _media_adapter().cleanup_loop()


def _extract_session_id(session: dict) -> str:
    """Compatibility wrapper for AgentService."""
    return AgentService.extract_session_id(session)


async def _ensure_session(telegram_user_id: str) -> str:
    """Compatibility wrapper for AgentService."""
    return await _agent_service.ensure_session(telegram_user_id)


async def _safe_reply(message, text: str) -> None:
    for attempt in range(3):
        try:
            await message.reply_text(text, disable_web_page_preview=True)
            return
        except Exception as exc:
            log.warning("فشل إرسال رد تيليغرام (محاولة %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                await asyncio.sleep(2**attempt)


async def _safe_html_reply(message, html: str, plain: str) -> None:
    """Deliver a page that is already Telegram HTML, retrying, then degrading to words.

    Renderers such as ``skills.py`` build finished HTML. Sending that through the
    Markdown renderer would escape the markup it already holds and the owner
    would read ``<b>heading</b>`` as literal text, so it goes out as HTML here and
    falls back to the plain twin only if Telegram refuses the format.
    """
    for attempt in range(3):
        try:
            await message.reply_text(
                html, parse_mode="HTML", disable_web_page_preview=True,
            )
            return
        except Exception as exc:
            log.warning("فشل إرسال صفحة HTML (محاولة %d/3): %s", attempt + 1, exc)
            if attempt < 2:
                await asyncio.sleep(2**attempt)
    if plain:
        await _safe_reply(message, plain)


# ═════════════════════════════════════════════════════════════════════
# لوحة المفاتيح الثابتة (Reply Keyboard) - أزرار مرئية أسفل المحادثة
# ═════════════════════════════════════════════════════════════════════


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """لوحة المفاتيح الثابتة مع الأوامر الرئيسية"""
    keyboard = [
        [
            KeyboardButton("🚀 بدء جلسة جديدة"),    # /start
            KeyboardButton("⏹️ إيقاف التنفيذ"),      # /abort
        ],
        [
            KeyboardButton("📋 مهامي"),              # /tasks
            KeyboardButton("✅ مهامي المكتملة"),      # /failed
        ],
        [
            KeyboardButton("📊 تقاريري"),            # /progress
            KeyboardButton("📈 مقاييسي"),           # /metrics
        ],
        [
            KeyboardButton("🗓️ جدولي"),             # /schedules
            KeyboardButton("📝 مسوداتي"),           # /drafts
        ],
        [
            KeyboardButton("🔍 البحث"),             # /search
            KeyboardButton("🤖 النموذج"),           # /model
        ],
        [
            KeyboardButton("❓ المساعدة"),          # /help
            KeyboardButton("📋 القائمة"),           # /menu
        ],
        [
            KeyboardButton("⌨️ إخفاء لوحة المفاتيح"), # /hidekeyboard
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="اختر أمراً من القائمة أو اكتب طلبك...",
    )


def get_hide_keyboard() -> ReplyKeyboardRemove:
    """إخفاء لوحة المفاتيح"""
    return ReplyKeyboardRemove()


async def _send_main_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """إرسال لوحة المفاتيح الثابتة للمستخدم"""
    await update.message.reply_text(
        "⌨️ **لوحة المفاتيح الرئيسية**\n\n"
        "اختر أمراً من الأزرار أدناه، أو اكتب طلبك مباشرة:\n\n"
        "💡 يمكنك إخفاء اللوحة باستخدام زر \"⌨️ إخفاء لوحة المفاتيح\"",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


async def cmd_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_main_keyboard(update, context)
async def cmd_hide_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("⌨️ تم إخفاء لوحة المفاتيح. يمكنك استعادتها باستخدام أمر /keyboard.", reply_markup=get_hide_keyboard())
async def _create_task_status_message(bot, chat_id: int, text: str = "جاري تجهيز الطلب…") -> int | None:
    """Create the single user-facing message that will be edited until completion."""
    try:
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            disable_web_page_preview=True,
        )
        return int(message.message_id)
    except Exception as exc:
        log.warning("تعذر إنشاء رسالة حالة الطلب: %s", type(exc).__name__)
        return None


async def _edit_task_status_message(bot, chat_id: int, message_id: int | None, text: str) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        log.debug("تعذر تحديث رسالة حالة الطلب: %s", type(exc).__name__)


def authorized[HandlerT](handler: HandlerT) -> HandlerT:
    """Compatibility decorator backed by the Telegram middleware layer."""
    return _access_controller().wrap(handler)


async def _create_fresh_session(user_id: str) -> str:
    """Compatibility wrapper for AgentService."""
    return await _agent_service.fresh_session(user_id)


def _parse_utc_datetime(value: str) -> datetime:
    """Compatibility wrapper; delegates to ``bridge.telegram.helpers.parse_utc_datetime``."""
    return parse_utc_datetime(value)


def _task_status_text(task: QueuedTask) -> str:
    """Compatibility wrapper; delegates to ``bridge.telegram.helpers.task_status_text``."""
    return task_status_text(task)


def _variant_for_model(model_id: str | None) -> str | None:
    """Compatibility wrapper for AgentService model-variant selection."""
    return _agent_service.variant_for_model(model_id)


async def _execute_agent_task(task: QueuedTask, bot) -> None:
    """Compatibility wrapper around the T02 execution service."""
    correlation_id = f"task:{task.id}"
    delivery = TelegramExecutionDelivery(
        bot,
        task_store,
        progress_store,
        live_reporters,
        max_message_length=MAX_MESSAGE_LENGTH,
        display_preferences=lambda owner_id: _display_preferences(owner_id),
        error_message=user_error,
        logger=log,
    )
    await _task_execution_service.execute(task, delivery, correlation_id=correlation_id)


@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().start(update, context)

@authorized
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().new(update, context)

@authorized
async def cmd_abort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().abort(update, context)

@authorized
async def cmd_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().direct(update, context)


@authorized
async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().direct(update, context, daily=True)


@authorized
async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().tasks(update, context)

@authorized
async def cmd_failed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().failed(update, context)

@authorized
async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().retry(update, context)

@authorized
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().cancel(update, context)

@authorized
async def cmd_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().schedules(update, context)


@authorized
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().schedule(update, context)


@authorized
async def cmd_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().repeat(update, context)


@authorized
async def cmd_schedhistory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().history(update, context)

@authorized
async def cmd_schedshow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().show(update, context)


@authorized
async def cmd_schedrename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().rename(update, context)


@authorized
async def cmd_schededit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().edit(update, context)


@authorized
async def cmd_schedappend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().append(update, context)


@authorized
async def cmd_schedtime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().change_time(update, context)


@authorized
async def cmd_schedtimezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().change_timezone(update, context)


@authorized
async def cmd_schedinterval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().change_interval(update, context)


@authorized
async def cmd_schedpause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().pause(update, context)


@authorized
async def cmd_schedresume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().resume(update, context)


@authorized
async def cmd_scheddelete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().delete(update, context)


@authorized
async def cmd_schedrun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _schedule_command_adapter().run_now(update, context)


@authorized
async def cmd_share(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().share(update, context)

@authorized
async def cmd_unshare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().unshare(update, context)

@authorized
async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().model(update, context)

@authorized
async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().progress(update, context)

@authorized
async def cmd_trace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().trace(update, context)

async def handle_reboot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reboot_callback_adapter().handle(update, context)


async def handle_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _model_picker_callbacks().handle(update, context)


@authorized
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _control_ui().show(update, context, persistent=True)


async def handle_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _panel_router().handle(update, context)


async def handle_agent_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _interaction_callbacks().handle(update, context)


async def handle_agent_interaction_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _interaction_callbacks().handle_reply(update, context)


async def handle_control_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _control_ui().handle(update, context)


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().status(update, context)

@authorized
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().health(update, context)

@authorized
async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _download_command_adapter().download(update, context)

@authorized
async def cmd_deletefile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _download_command_adapter().delete(update, context)

@authorized
async def cmd_mirror(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _manage_command_adapter().mirror(update, context)

@authorized
async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _manage_command_adapter().prompt(update, context)

@authorized
async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _manage_command_adapter().topics(update, context)

@authorized
async def cmd_taskshow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _manage_command_adapter().taskshow(update, context)

@authorized
async def cmd_dailies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _manage_command_adapter().dailies(update, context)

@authorized
async def cmd_exec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _shell_command_adapter().exec(update, context)

@authorized
async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().agents(update, context)


@authorized
async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _skill_command_adapter().skills(update, context)

@authorized
async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _system_command_adapter().maintenance(update, context)

@authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _system_command_adapter().help(update, context)


@authorized
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _config_command_adapter().config(update, context)


@authorized
async def cmd_limits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _config_command_adapter().limits(update, context)


@authorized
async def cmd_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _metrics_command_adapter().metrics(update, context)


@authorized
async def cmd_research_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().research(update, context)

@authorized
async def cmd_discard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _media_adapter().discard(update, context)


@authorized
async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _draft_intake_adapter().attachment(update, context)

@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _draft_intake_adapter().text(update, context)

async def post_init(app: Application) -> None:
    global task_service, model_manager, pending_cleanup_task, skill_sync_task
    attachment_store.ensure_directories()
    if task_service is None:
        await task_store.init()
        task_service = TaskService(task_store, lambda task: _execute_agent_task(task, app.bot))
        interrupted = await task_service.start()
        if interrupted:
            audit.write("task_recovery", "interrupted", details={"count": interrupted})
            log.warning("تمت معالجة %s مهمة منقطعة: استئناف المهام المحفوظة وفحص المهام القديمة.", interrupted)
    if pending_cleanup_task is None or pending_cleanup_task.done():
        pending_cleanup_task = asyncio.create_task(_pending_attachment_cleanup_loop())
    if skill_sync_task is None or skill_sync_task.done():
        skill_sync_task = asyncio.create_task(_skill_sync_loop())
    _schedule_download_cleanup()
    if model_manager is None:
        model_manager = ModelManager(
            client=client,
            store=store,
            audit=audit,
            fallback_model=DEFAULT_MODEL,
            sync_seconds=MODEL_CATALOG_SYNC_SECONDS,
            pin_default_model=PIN_DEFAULT_MODEL,
        )
        model_manager.set_owner_pin_reader(_model_selection().pinned_model)
        model_manager.set_owner_variant_reader(
            lambda owner_id, model_id: _owner_variant_choice(owner_id, model_id)
        )
        await model_manager.start()
    try:
        health = await client.health()
        # Backup after stable startup (automatic, complete, final).
        try:
            await backup_adapter.run()
        except Exception as exc:
            log.info("automatic backup skipped: %s", type(exc).__name__)
        if health.get("healthy"):
            log.info("وكيل OpenCode متاح (الإصدار %s).", health.get("version", "غير معروف"))
        else:
            log.warning("وكيل OpenCode استجاب دون حالة سليمة.")
    except Exception as exc:
        log.warning("وكيل OpenCode غير متاح عند بدء التشغيل: %s", exc)

    await app.bot.set_my_commands(core_commands())
    # The agent's permission requests and questions need a bot to answer them
    # with; the adapter is built before the application exists.
    _interaction_callbacks().attach_bot(app.bot)
    log.info(
        "تم تسجيل أوامر البوت. النموذج: %s، الاستدلال: %s، الوكيل: %s، وكيل تيليغرام: %s",
        DEFAULT_MODEL or "تلقائي (أفضل مجاني متاح)",
        DEFAULT_MODEL_VARIANT or "default",
        DEFAULT_AGENT,
        "مفعّل" if TELEGRAM_PROXY_URL else "غير مفعّل",
    )

    # Initialize GitHub command handler if token is configured
    global github_handler
    if SETTINGS.github.token and github_handler is None:
        try:
            from bridge.services.github.commands import GitHubCommandHandler

            github_handler = GitHubCommandHandler(
                github_token=SETTINGS.github.token,
                workspace_root=BRIDGE_DIR / "github-workspaces",
            )
            await github_handler._ensure_client()
            log.info("تم تسجيل أوامر GitHub (ghrepos, ghcreate, ghclone, ghstatus, ghpr, ghissue, ghrelease, ghbranch, ghsecret, ghhelp)")
        except Exception as exc:
            log.warning("تعذر تهيئة معالج أوامر GitHub: %s", exc)
            github_handler = None


async def post_shutdown(app: Application) -> None:
    global task_service, model_manager, pending_cleanup_task, skill_sync_task
    if pending_cleanup_task is not None:
        pending_cleanup_task.cancel()
        try:
            await pending_cleanup_task
        except asyncio.CancelledError:
            pass
        pending_cleanup_task = None
    if skill_sync_task is not None:
        skill_sync_task.cancel()
        try:
            await skill_sync_task
        except asyncio.CancelledError:
            pass
        skill_sync_task = None
    if _media_adapter_instance is not None:
        await _media_adapter_instance.shutdown()
    if model_manager is not None:
        await model_manager.stop()
        model_manager = None
    if task_service is not None:
        await task_service.stop()
        task_service = None
    await client.close()
    await task_store.close()
    await store.close()
    # Close GitHub API client
    global github_handler
    if github_handler is not None:
        await github_handler.close()
        github_handler = None
    log.info("أُغلقت اتصالات البوت بأمان.")


async def main() -> None:
    # Bounded pools for a ~1GB host: a private bot never needs PTB's 256
    # pooled connections per request object. Generous media write timeout so
    # 49MB part uploads on slow uplinks are not cut off after 20s.
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        connection_pool_size=32,
        media_write_timeout=300.0,
        http_version="1.1",
        proxy=TELEGRAM_PROXY_URL,
    )
    updates_request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        connection_pool_size=32,
        media_write_timeout=300.0,
        http_version="1.1",
        proxy=TELEGRAM_PROXY_URL,
    )
    app = build_application(
        token=TELEGRAM_BOT_TOKEN,
        request=request,
        updates_request=updates_request,
        post_init=post_init,
        post_shutdown=post_shutdown,
        base_url=SETTINGS.telegram.local_api_base_url if SETTINGS.telegram.api_mode == "local" else None,
        base_file_url=SETTINGS.telegram.local_file_base_url if SETTINGS.telegram.api_mode == "local" else None,
        local_mode=SETTINGS.telegram.api_mode == "local",
    )
    register_core_handlers(
        app,
        sys.modules[__name__],
        tuple(RESEARCH_COMMAND_MODES),
    )

    await store.init()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    log.info("جاري الاتصال بتيليغرام…")
    for attempt in range(1, 11):
        try:
            await app.initialize()
            await post_init(app)
            break
        except Exception as exc:
            wait_seconds = min(attempt * 5, 60)
            log.warning("فشلت محاولة الاتصال %d/10: %s. إعادة المحاولة بعد %d ثانية.", attempt, exc, wait_seconds)
            if attempt == 10:
                raise
            await asyncio.sleep(wait_seconds)

    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=DROP_PENDING_UPDATES)
    log.info("البوت يعمل. النموذج: %s، الاستدلال: %s، الوكيل: %s", DEFAULT_MODEL or "تلقائي (أفضل مجاني متاح)", DEFAULT_MODEL_VARIANT or "default", DEFAULT_AGENT)
    await stop_event.wait()
    await app.updater.stop()
    await app.stop()
    await post_shutdown(app)
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

# ------------------------------------------------------------------
# Reminder / notification integration (sequential + scheduled)
# ------------------------------------------------------------------
_reminder_service: Any | None = None
_reminder_scheduler: Any | None = None

def _reminders() -> tuple[Any, Any]:
    global _reminder_service, _reminder_scheduler
    if _reminder_service is None:
        from bridge.services.reminder_service import (
            create_reminder_service,
        )
        # Use the same BridgeDatabase instance as task_store (shared connection).
        _reminder_service, _reminder_scheduler = create_reminder_service(
            task_store.database if hasattr(task_store, "database") else None,
            transport=_reminder_transport(),
        )
    return _reminder_service, _reminder_scheduler

def _reminder_transport() -> Any:
    """Thin Telegram transport adapter for reminders."""
    class TelegramReminderTransport:
        async def send_text(self, destination: str | int, text: str) -> Any:
            try:
                bot = bot_ref.get("bot")
                if bot is not None:
                    await bot.send_message(chat_id=int(destination), text=text)
            except Exception:
                pass
    return TelegramReminderTransport()

# Module-level reference container so transport can access the bot.
bot_ref: dict[str, Any] = {}

def _register_bot_ref(app: Any) -> None:
    bot_ref["bot"] = app.bot if hasattr(app, "bot") else None

async def cmd_remind(update: Any, context: Any) -> None:
    """/remind <message> [at HH:MM] — queue a reminder; if running, queued."""
    from datetime import UTC, datetime, timedelta
    args = (context.args or [])
    if not args:
        await update.message.reply_text("استخدم: /remind نص التذكير [HH:MM]")
        return
    prompt = " ".join(args[:1])
    at_str = args[-1] if len(args) > 1 else None
    remind_at = datetime.now(UTC) + timedelta(minutes=5) if at_str is None else datetime.now(UTC).replace(hour=int(at_str.split(":")[0]), minute=int(at_str.split(":")[1]))
    # Use sequential store for reminder queuing (if needed) or direct create
    store, scheduler = _reminders()
    reminder_id = await store.create(str(update.effective_user.id), prompt, remind_at)
    await update.message.reply_text(f"✅ تم جدولة التذكير (#{reminder_id}) عند {remind_at:%H:%M}")
    # Start scheduler if not running
    await scheduler.start()

async def cmd_reminders(update: Any, context: Any) -> None:
    store, _ = _reminders()
    items = await store.list_for_owner(str(update.effective_user.id), limit=10)
    if not items:
        await update.message.reply_text("لا توجد تذكيرات.")
        return
    lines = ["🕐 قائمة المهام — التذكيرات:"]
    for r in items:
        status_icon = "✅" if r.status == "sent" else ("⏳" if r.status == "pending" else "❌")
        lines.append(f"{status_icon} #{r.id}: {r.message} @ {r.remind_at:%Y-%m-%d %H:%M}")
    await update.message.reply_text("\n".join(lines[:20]))
