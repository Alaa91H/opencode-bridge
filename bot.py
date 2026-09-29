"""Arabic Telegram bridge for a locally hosted OpenCode agent."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import signal
import sys

import httpx
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes
from telegram.request import HTTPXRequest

BRIDGE_DIR = Path(__file__).parent
MAINTENANCE_REPORT_PATH = BRIDGE_DIR / "runtime" / "maintenance-latest.md"
ATTACHMENT_ROOT = BRIDGE_DIR / "runtime" / "attachments"
sys.path.insert(0, str(BRIDGE_DIR))

from attachments import AttachmentError, AttachmentStore, attachment_prompt_note
from audit_log import AuditLogger
from block_patterns import check_build, check_hardline
from bridge.config import get_settings
from bridge.domain.policies import RequestGuard
from bridge.domain.schedules import (
    format_interval as schedule_format_interval,
    parse_interval_seconds as schedule_parse_interval_seconds,
    parse_utc_datetime as schedule_parse_utc_datetime,
    split_pipe_args as schedule_split_pipe_args,
)
from bridge.services.agent_service import AgentService
from bridge.services.config_service import ConfigurationService
from bridge.services.draft_service import DraftService
from bridge.infrastructure.database.draft_store import DraftStore
from bridge.infrastructure.database.user_settings_store import UserSettingsStore
from bridge.services.model_selection_service import ModelSelectionService
from bridge.services.user_preferences_service import UserPreferencesService
from bridge.telegram.callbacks.model_picker import ModelPickerCallbackAdapter
from bridge.telegram.panels import (
    MENU,
    PATTERN as PANEL_PATTERN,
    PanelContext,
    PanelRouter,
    build_panels,
)
from bridge.services.maintenance_service import MaintenanceReportService
from bridge.services.media_service import MediaTaskService
from bridge.services.schedule_service import ScheduleService
from bridge.services.task_service import TaskApplicationService
from bridge.services.task_execution_service import TaskExecutionService
from bridge.telegram.app import build_application, core_commands, register_core_handlers
from bridge.telegram.attachments import TelegramMediaAdapter
from bridge.telegram.callbacks.reboot import RebootCallbackAdapter
from bridge.telegram.commands.agent import AgentCommands
from bridge.telegram.commands.config import ConfigCommands
from bridge.telegram.commands.drafts import DraftCommands
from bridge.telegram.commands.schedules import ScheduleCommands
from bridge.telegram.callbacks.schedules import ScheduleCallbacks
from bridge.telegram.commands.system import SystemCommands
from bridge.telegram.commands.tasks import TaskCommands
from bridge.telegram.execution import TelegramExecutionDelivery
from bridge.telegram.draft_intake import DraftIntakeAdapter
from bridge.telegram.middleware import TelegramAccessController
from bridge.telegram.rendering.schedules import scheduled_job_line
from formatter import MAX_MESSAGE_LENGTH
from model_catalog import ranked_zen_general_model_ids
from model_manager import ModelManager
from messages import (
    HELP_TEXT,
    build_blocked_message,
    empty_response_message,
    startup_message,
    unauthorized_message,
    user_error,
)
from opencode_client import OpenCodeClient, extract_file_response, extract_text_response
from progress import ProgressStore, render_persisted_activity, render_progress
from progress_reporter import LiveProgressReporter
from prompt_enhancer import ResearchMode, enhance_prompt
from reboot_state import decision_path, read_state, request_path, write_state
from session_store import SessionStore
from task_queue import QueuedTask, TaskQueueStore
from task_service import TaskService

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
ALLOWED_CHAT_IDS = set(SETTINGS.telegram.allowed_chat_ids)
OPENCODE_HOST = SETTINGS.opencode.host
OPENCODE_PORT = SETTINGS.opencode.port
OPENCODE_PASSWORD = SETTINGS.opencode.password
DEFAULT_MODEL = SETTINGS.opencode.default_model
DEFAULT_MODEL_VARIANT = SETTINGS.opencode.model_variant
VARIANT_MODEL = SETTINGS.opencode.variant_model
AUTO_STRONGEST_FREE_MODEL = SETTINGS.features.auto_strongest_free_model
PIN_DEFAULT_MODEL = SETTINGS.effective_pin_default_model
DEFAULT_AGENT = SETTINGS.opencode.agent
MODEL_CATALOG_SYNC_SECONDS = SETTINGS.opencode.model_sync_seconds
ATTACHMENT_MAX_BYTES = SETTINGS.telegram.attachment_max_bytes
ATTACHMENT_MAX_COUNT = SETTINGS.telegram.attachment_max_count
ATTACHMENT_MAX_TOTAL_BYTES = SETTINGS.telegram.attachment_max_total_bytes
ATTACHMENT_PENDING_SECONDS = SETTINGS.telegram.attachment_pending_seconds
MEDIA_GROUP_DEBOUNCE_SECONDS = SETTINGS.telegram.media_group_debounce_seconds

store = SessionStore(BRIDGE_DIR / "sessions.db")
task_store = TaskQueueStore(BRIDGE_DIR / "sessions.db")
client = OpenCodeClient(
    host=OPENCODE_HOST,
    port=OPENCODE_PORT,
    password=OPENCODE_PASSWORD,
)
audit = AuditLogger(BRIDGE_DIR / "runtime" / "audit.jsonl")
attachment_store = AttachmentStore(
    ATTACHMENT_ROOT,
    max_bytes=ATTACHMENT_MAX_BYTES,
    max_count=ATTACHMENT_MAX_COUNT,
    max_total_bytes=ATTACHMENT_MAX_TOTAL_BYTES,
)
progress_store = ProgressStore()
live_reporters: dict[int, LiveProgressReporter] = {}
task_service: TaskService | None = None
model_manager: ModelManager | None = None
pending_cleanup_task: asyncio.Task[None] | None = None
_media_adapter_instance: TelegramMediaAdapter | None = None
_task_commands_instance: TaskCommands | None = None
_schedule_commands_instance: ScheduleCommands | None = None
_agent_commands_instance: AgentCommands | None = None
_system_commands_instance: SystemCommands | None = None
_config_commands_instance: ConfigCommands | None = None
_draft_commands_instance: DraftCommands | None = None
_draft_intake_instance: DraftIntakeAdapter | None = None
_reboot_callback_instance: RebootCallbackAdapter | None = None
_model_picker_callback_instance: ModelPickerCallbackAdapter | None = None
_model_selection_service: ModelSelectionService | None = None
_user_preferences_service: UserPreferencesService | None = None
_panel_router_instance: PanelRouter | None = None
_access_controller_instance: TelegramAccessController | None = None
_request_guard = RequestGuard((check_build, check_hardline))
_agent_service = AgentService(
    client,
    store,
    default_model=DEFAULT_MODEL,
    default_agent=DEFAULT_AGENT,
    default_variant=DEFAULT_MODEL_VARIANT,
    variant_model=VARIANT_MODEL,
    model_manager_provider=lambda: model_manager,
    logger=log,
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
UTC = timezone.utc

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


def _reboot_callback_adapter() -> RebootCallbackAdapter:
    global _reboot_callback_instance
    if _reboot_callback_instance is None:
        _reboot_callback_instance = RebootCallbackAdapter(
            is_allowed=_is_allowed,
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

    return {
        ("system", "refresh"): noop,
        ("tasks", "refresh"): noop,
        ("schedules", "refresh"): noop,
        ("tasks", "cancel"): cancel_task,
        ("tasks", "retry"): retry_task,
        ("schedules", "toggle_schedule"): toggle_schedule,
        ("schedules", "delete_schedule"): delete_schedule,
    }


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
        _draft_commands_instance = DraftCommands(_draft_service, _safe_reply)
    return _draft_commands_instance

async def cmd_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _draft_command_adapter().handle(update, context)


def _draft_intake_adapter() -> DraftIntakeAdapter:
    global _draft_intake_instance
    if _draft_intake_instance is None:
        _draft_intake_instance = DraftIntakeAdapter(
            _draft_service, _media_adapter(), attachment_store, _safe_reply, _task_command_adapter()
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
            selection_service=_model_selection(),
            picker_callbacks=_model_picker_callbacks(),
            logger=log,
        )
    return _agent_commands_instance


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
        _config_commands_instance = ConfigCommands(
            _configuration_service,
            reply=_safe_reply,
        )
    return _config_commands_instance


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


def authorized(handler: F) -> F:
    """Compatibility decorator backed by the Telegram middleware layer."""
    return _access_controller().wrap(handler)


async def _create_fresh_session(user_id: str) -> str:
    """Compatibility wrapper for AgentService."""
    return await _agent_service.fresh_session(user_id)


def _parse_utc_datetime(value: str) -> datetime:
    """Compatibility wrapper for the T02 scheduling domain parser."""
    return schedule_parse_utc_datetime(value)


def _task_status_text(task: QueuedTask) -> str:
    labels = {
        "queued": "بانتظار التنفيذ",
        "scheduled": "مجدولة",
        "running": "قيد التنفيذ",
        "completed": "مكتملة",
        "failed": "فشلت",
        "cancelled": "ملغاة",
    }
    return labels.get(task.status, task.status)


def _variant_for_model(model_id: str | None) -> str | None:
    """Compatibility wrapper for AgentService model-variant selection."""
    return _agent_service.variant_for_model(model_id)


async def _execute_agent_task(task: QueuedTask, bot) -> None:
    """Compatibility wrapper around the T02 execution service."""
    delivery = TelegramExecutionDelivery(
        bot,
        task_store,
        progress_store,
        live_reporters,
        max_message_length=MAX_MESSAGE_LENGTH,
        error_message=user_error,
        logger=log,
    )
    await _task_execution_service.execute(task, delivery)


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
    await _panel_router().show(update)


async def handle_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _panel_router().handle(update, context)


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().status(update, context)

@authorized
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().health(update, context)

@authorized
async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _agent_command_adapter().agents(update, context)

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
    global task_service, model_manager, pending_cleanup_task
    attachment_store.ensure_directories()
    if task_service is None:
        await task_store.init()
        task_service = TaskService(task_store, lambda task: _execute_agent_task(task, app.bot))
        interrupted = await task_service.start()
        if interrupted:
            audit.write("task_recovery", "interrupted", details={"count": interrupted})
            log.warning("تم تعليم %s مهمة كفاشلة بعد انقطاع سابق.", interrupted)
    if pending_cleanup_task is None or pending_cleanup_task.done():
        pending_cleanup_task = asyncio.create_task(_pending_attachment_cleanup_loop())
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
        if health.get("healthy"):
            log.info("وكيل OpenCode متاح (الإصدار %s).", health.get("version", "غير معروف"))
        else:
            log.warning("وكيل OpenCode استجاب دون حالة سليمة.")
    except Exception as exc:
        log.warning("وكيل OpenCode غير متاح عند بدء التشغيل: %s", exc)

    await app.bot.set_my_commands(core_commands())
    log.info(
        "تم تسجيل أوامر البوت. النموذج: %s، الاستدلال: %s، الوكيل: %s، وكيل تيليغرام: %s",
        DEFAULT_MODEL,
        DEFAULT_MODEL_VARIANT or "default",
        DEFAULT_AGENT,
        "مفعّل" if TELEGRAM_PROXY_URL else "غير مفعّل",
    )


async def post_shutdown(app: Application) -> None:
    global task_service, model_manager, pending_cleanup_task
    if pending_cleanup_task is not None:
        pending_cleanup_task.cancel()
        try:
            await pending_cleanup_task
        except asyncio.CancelledError:
            pass
        pending_cleanup_task = None
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
    log.info("أُغلقت اتصالات البوت بأمان.")


async def main() -> None:
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        http_version="1.1",
        proxy=TELEGRAM_PROXY_URL,
    )
    updates_request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=60.0,
        pool_timeout=60.0,
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
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
    log.info("البوت يعمل. النموذج: %s، الاستدلال: %s، الوكيل: %s", DEFAULT_MODEL, DEFAULT_MODEL_VARIANT or "default", DEFAULT_AGENT)
    await stop_event.wait()
    await app.updater.stop()
    await app.stop()
    await post_shutdown(app)
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
