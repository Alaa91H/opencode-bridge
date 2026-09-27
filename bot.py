"""Arabic Telegram bridge for a locally hosted OpenCode agent."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys

import httpx
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from bridge.domain.policies import RequestGuard
from bridge.domain.schedules import (
    format_interval as schedule_format_interval,
    parse_interval_seconds as schedule_parse_interval_seconds,
    parse_utc_datetime as schedule_parse_utc_datetime,
    split_pipe_args as schedule_split_pipe_args,
)
from bridge.services.agent_service import AgentService
from bridge.services.media_service import MediaTaskService
from bridge.services.schedule_service import ScheduleService
from bridge.services.task_service import TaskApplicationService
from bridge.telegram.app import build_application, core_commands, register_core_handlers
from bridge.telegram.attachments import TelegramMediaAdapter
from bridge.telegram.callbacks.reboot import RebootCallbackAdapter
from bridge.telegram.commands.agent import AgentCommands
from bridge.telegram.commands.schedules import ScheduleCommands
from bridge.telegram.commands.tasks import TaskCommands
from bridge.telegram.middleware import TelegramAccessController
from bridge.telegram.rendering.schedules import scheduled_job_line
from formatter import MAX_MESSAGE_LENGTH
from free_points import FreePointsTracker, format_free_points_header
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

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)
log = logging.getLogger("opencode_bridge")


def _load_env(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting service variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(BRIDGE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# Optional HTTP(S) or SOCKS proxy used exclusively for Telegram API traffic.
TELEGRAM_PROXY_URL = os.environ.get("TELEGRAM_PROXY_URL", "").strip() or None
ALLOWED_USERS = {
    int(value.strip())
    for value in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
    if value.strip()
}
ALLOWED_CHAT_IDS = {
    int(value.strip())
    for value in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
    if value.strip()
}
OPENCODE_HOST = os.environ.get("OPENCODE_HOST", "127.0.0.1")
OPENCODE_PORT = int(os.environ.get("OPENCODE_PORT", "4096"))
OPENCODE_PASSWORD = os.environ.get("OPENCODE_PASSWORD")
DEFAULT_MODEL = os.environ.get("OPENCODE_DEFAULT_MODEL", "opencode/muse-spark-1.3-contributor-free")
DEFAULT_MODEL_VARIANT = os.environ.get("OPENCODE_MODEL_VARIANT", "xhigh").strip() or None
VARIANT_MODEL = os.environ.get("OPENCODE_VARIANT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
AUTO_STRONGEST_FREE_MODEL = os.environ.get("AGENT_SCOUT_AUTO_STRONGEST", "1").strip().lower() not in {"0", "false", "no", "off"}
PIN_DEFAULT_MODEL = (
    os.environ.get("OPENCODE_PIN_DEFAULT_MODEL", "1").strip().lower() not in {"0", "false", "no", "off"}
    and not AUTO_STRONGEST_FREE_MODEL
)
DEFAULT_AGENT = os.environ.get("OPENCODE_AGENT", "telegram-operator")
MODEL_CATALOG_SYNC_SECONDS = max(60, int(os.environ.get("OPENCODE_MODEL_SYNC_SECONDS", "900")))
ATTACHMENT_MAX_BYTES = max(1, int(os.environ.get("TELEGRAM_ATTACHMENT_MAX_BYTES", str(20 * 1024 * 1024))))
ATTACHMENT_MAX_COUNT = max(1, int(os.environ.get("TELEGRAM_ATTACHMENT_MAX_COUNT", "10")))
ATTACHMENT_MAX_TOTAL_BYTES = max(
    ATTACHMENT_MAX_BYTES,
    int(os.environ.get("TELEGRAM_ATTACHMENT_MAX_TOTAL_BYTES", str(50 * 1024 * 1024))),
)
ATTACHMENT_PENDING_SECONDS = max(60, int(os.environ.get("TELEGRAM_ATTACHMENT_PENDING_SECONDS", "600")))
MEDIA_GROUP_DEBOUNCE_SECONDS = max(
    0.25,
    min(float(os.environ.get("TELEGRAM_MEDIA_GROUP_DEBOUNCE_SECONDS", "1.25")), 5.0),
)
FREE_DAILY_POINTS = max(1, int(os.environ.get("OPENCODE_FREE_DAILY_POINTS", "200")))
DAILY_TASK_COUNTER_TIMEZONE_NAME = os.environ.get("TELEGRAM_DAILY_TASK_COUNTER_TIMEZONE", "Etc/GMT-2").strip()
try:
    DAILY_TASK_COUNTER_TIMEZONE = ZoneInfo(DAILY_TASK_COUNTER_TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(
        "TELEGRAM_DAILY_TASK_COUNTER_TIMEZONE يجب أن تكون منطقة زمنية IANA صالحة، مثل Etc/GMT-2 أو Europe/Paris"
    ) from exc

store = SessionStore(BRIDGE_DIR / "sessions.db")
task_store = TaskQueueStore(BRIDGE_DIR / "sessions.db")
free_points_tracker = FreePointsTracker(
    BRIDGE_DIR / "runtime" / "free-points.db",
    daily_limit=FREE_DAILY_POINTS,
    timezone_name=DAILY_TASK_COUNTER_TIMEZONE_NAME,
)
client = OpenCodeClient(
    host=OPENCODE_HOST,
    port=OPENCODE_PORT,
    password=OPENCODE_PASSWORD,
    free_points_tracker=free_points_tracker,
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
_reboot_callback_instance: RebootCallbackAdapter | None = None
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
            logger=log,
        )
    return _agent_commands_instance


async def _pending_attachment_cleanup_loop() -> None:
    await _media_adapter().cleanup_loop()


def _extract_session_id(session: dict) -> str:
    """Compatibility wrapper for AgentService."""
    return AgentService.extract_session_id(session)


async def _ensure_session(telegram_user_id: str) -> str:
    """Compatibility wrapper for AgentService."""
    return await _agent_service.ensure_session(telegram_user_id)


async def _typing_loop(chat_id: int | None, bot, stop_event: asyncio.Event) -> None:
    try:
        while not stop_event.is_set() and chat_id:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)
    except Exception as exc:  # Telegram failures must not cancel agent work.
        log.debug("تعذر تحديث مؤشر الكتابة: %s", exc)


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


async def _send_task_output_files(task: QueuedTask, bot) -> int:
    """Send only regular files created inside this task's managed output directory."""
    sent = 0
    for path in attachment_store.collect_task_outputs(task.id):
        with path.open("rb") as handle:
            await bot.send_document(
                chat_id=task.chat_id,
                document=handle,
                filename=path.name,
                caption="ملف ناتج عن الطلب" if sent == 0 else None,
            )
        sent += 1
    return sent


def _response_usage_points(response: dict | None) -> int:
    if not isinstance(response, dict):
        return 0
    value = response.get("_bridge_usage_points", 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _task_reply_text(text: str, command_points: int) -> str:
    """Build one final Telegram message and stay within Telegram's hard text limit."""
    header = format_free_points_header(free_points_tracker.snapshot(), command_points)
    body_limit = max(512, MAX_MESSAGE_LENGTH - len(header) - 2)
    body = text.strip() or empty_response_message()
    if len(body) > body_limit:
        suffix = "\n\n… تم اختصار الرد بسبب حد طول رسالة Telegram."
        body = body[: max(1, body_limit - len(suffix))].rstrip() + suffix
    return f"{header}\n\n{body}"


def _variant_for_model(model_id: str | None) -> str | None:
    """Compatibility wrapper for AgentService model-variant selection."""
    return _agent_service.variant_for_model(model_id)


async def _send_prompt_with_model_fallback(
    task: QueuedTask,
    session_id: str,
    prompt: str,
    parts: list[dict],
    selected_model: str | None,
) -> tuple[dict, str | None, int]:
    """Send with variant, attachment-transport, and model fallbacks.

    Some providers accept images directly but reject video, archive, or other
    file parts. Every attachment is also exposed through a validated local path
    in the prompt, so a transport-level file rejection can safely retry without
    file parts while preserving server-side agent access to the same files.
    """

    async def send_for_model(model_id: str | None) -> dict:
        selected_variant = _variant_for_model(model_id)
        variant = selected_variant
        try:
            return await client.send_prompt(
                session_id,
                prompt,
                model=model_id,
                agent=DEFAULT_AGENT,
                parts=parts,
                variant=variant,
            )
        except httpx.HTTPStatusError as exc:
            if variant and exc.response.status_code in {400, 404, 422}:
                audit.write(
                    "model_variant_fallback",
                    "retry_default",
                    actor_id=task.owner_id,
                    details={"model": model_id, "variant": variant},
                )
                variant = None
                try:
                    return await client.send_prompt(
                        session_id,
                        prompt,
                        model=model_id,
                        agent=DEFAULT_AGENT,
                        parts=parts,
                        variant=None,
                    )
                except httpx.HTTPStatusError as retry_exc:
                    exc = retry_exc

            if parts and exc.response.status_code in {400, 413, 415, 422}:
                audit.write(
                    "attachment_transport_fallback",
                    "local_path",
                    actor_id=task.owner_id,
                    details={
                        "task_id": task.id,
                        "model": model_id,
                        "status_code": exc.response.status_code,
                        "attachment_count": len(parts),
                    },
                )
                try:
                    return await client.send_prompt(
                        session_id,
                        prompt,
                        model=model_id,
                        agent=DEFAULT_AGENT,
                        parts=[],
                        variant=variant,
                    )
                except httpx.HTTPStatusError as retry_exc:
                    exc = retry_exc
            raise exc

    try:
        response = await send_for_model(selected_model)
        return response, selected_model, _response_usage_points(response)
    except httpx.HTTPStatusError as exc:
        if model_manager is None or selected_model is None or exc.response.status_code not in {400, 404, 422}:
            raise
        fallback_model = await model_manager.ensure_session_model(
            task.owner_id,
            session_id,
            selected_model,
            excluded_ids={selected_model},
        )
        if fallback_model == selected_model:
            raise
        audit.write(
            "model_auto_switched",
            "fallback",
            actor_id=task.owner_id,
            details={"from_model": selected_model, "to_model": fallback_model, "reason": "inference_model_unavailable"},
        )
        response = await send_for_model(fallback_model)
        return response, fallback_model, _response_usage_points(response)


async def _execute_agent_task(task: QueuedTask, bot) -> None:
    """Execute one task while publishing safe, live operational milestones."""
    current = await task_store.get(task.id)
    if current is None or current.status == "cancelled":
        return

    reporter = LiveProgressReporter(task, bot, task_store, progress_store)
    live_reporters[task.id] = reporter
    stop_typing = asyncio.Event()
    typing_task: asyncio.Task[None] | None = None
    event_task: asyncio.Task[None] | None = None
    command_points = 0
    try:
        await reporter.start()
        requested_mode: ResearchMode | None = None
        if task.execution_mode:
            try:
                requested_mode = ResearchMode(task.execution_mode)
            except ValueError:
                log.warning("تم تجاهل وضع تنفيذ غير معروف للمهمة %s", task.id)
        enhanced = enhance_prompt(task.prompt, requested_mode=requested_mode)
        audit.write(
            "task_started",
            "accepted",
            actor_id=task.owner_id,
            details={
                "task_id": task.id,
                "scheduled": task.is_recurring,
                "attachment_count": len(task.attachments),
                "intent": enhanced.intent,
                "research_depth": enhanced.research_depth,
                "requested_mode": requested_mode,
            },
        )
        await reporter.record("preparing", "عم نتحقق من المرفقات ونحضّر مساحة النتيجة.")
        attachments = attachment_store.validate_input_records(task.attachments)
        output_directory = attachment_store.task_output_directory(task.id)
        work_directory = attachment_store.task_work_directory(task.id)
        prompt = enhanced.text + "\n\n" + attachment_prompt_note(
            attachments,
            output_directory,
            work_directory,
        )
        message_parts = [
            attachment.to_message_part()
            for attachment in attachments
            if attachment.is_direct_model_visible()
        ]
        session_id = await _ensure_session(task.owner_id)
        session = await store.get_session(task.owner_id)
        selected_model = session.model if session else DEFAULT_MODEL
        needs_image_input = any(
            attachment.mime.startswith("image/")
            or attachment.mime == "application/pdf"
            or attachment.kind in {"video", "animation", "video_note"}
            for attachment in attachments
        )
        if needs_image_input and model_manager is not None:
            try:
                media_model = await model_manager.best_available_for_inputs({"image"})
            except Exception as exc:
                media_model = None
                log.info("تعذر اختيار نموذج صور خاص بالمهمة: %s", type(exc).__name__)
            if media_model and media_model != selected_model:
                audit.write(
                    "task_media_model_selected",
                    "changed",
                    actor_id=task.owner_id,
                    details={
                        "task_id": task.id,
                        "from_model": selected_model,
                        "to_model": media_model,
                        "required_input": "image",
                    },
                )
                selected_model = media_model
        await reporter.record("session", "تم تجهيز جلسة الوكيل. عم نبدأ التنفيذ.")
        typing_task = asyncio.create_task(_typing_loop(task.chat_id, bot, stop_typing))
        event_task = asyncio.create_task(reporter.consume_events(client, session_id))
        started_at = time.monotonic()
        await reporter.record("processing", "الوكيل استلم المهمة وعم يعالجها.")
        response, selected_model, usage_points = await _send_prompt_with_model_fallback(
            task,
            session_id,
            prompt,
            message_parts,
            selected_model,
        )
        command_points += usage_points
        reply_text = extract_text_response(response)
        output_files = attachment_store.collect_task_outputs(task.id)
        if not reply_text and not output_files:
            await reporter.record("retry", "ما وصل ناتج واضح؛ عم نجرب مرة أخيرة.", "warning", force=True)
            response, selected_model, usage_points = await _send_prompt_with_model_fallback(
                task,
                session_id,
                prompt,
                message_parts,
                selected_model,
            )
            command_points += usage_points
            reply_text = extract_text_response(response)
            output_files = attachment_store.collect_task_outputs(task.id)
        elapsed = time.monotonic() - started_at
        current = await task_store.get(task.id)
        if current is None or current.status == "cancelled":
            await reporter.finalize_text(
                "تم إلغاء الطلب.",
                status="cancelled",
                message="تم إلغاء الطلب قبل تسليم النتيجة.",
            )
            audit.write("task_cancelled", "cancelled", actor_id=task.owner_id, details={"task_id": task.id})
            return

        if not reply_text and not output_files:
            await task_store.finish(task.id, success=False, error="empty_response")
            await reporter.finalize_text(
                _task_reply_text(empty_response_message(), command_points),
                status="failed",
                message="لم يرجع الوكيل نتيجة واضحة بعد إعادة المحاولة.",
            )
            audit.write(
                "task_finished",
                "empty_response",
                actor_id=task.owner_id,
                details={
                    "task_id": task.id,
                    "duration_seconds": round(elapsed, 2),
                    "agent_file_parts": len(extract_file_response(response)),
                },
            )
            return

        await reporter.record("delivering", "جاري تجهيز النتيجة النهائية…", force=True)
        delivered_files = await _send_task_output_files(task, bot)
        final_body = reply_text or (
            "تم تجهيز الملفات المطلوبة."
            if delivered_files
            else empty_response_message()
        )
        await task_store.finish(task.id, success=True)
        await reporter.finalize_text(
            _task_reply_text(final_body, command_points),
            status="completed",
            message="اكتمل التنفيذ.",
        )
        audit.write(
            "task_finished",
            "success",
            actor_id=task.owner_id,
            details={
                "task_id": task.id,
                "duration_seconds": round(elapsed, 2),
                "response_length": len(reply_text),
                "attachment_count": len(attachments),
                "output_files": delivered_files,
                "agent_file_parts": len(extract_file_response(response)),
                "free_points_used": command_points,
                "free_points_remaining": free_points_tracker.snapshot().remaining,
            },
        )
    except AttachmentError as exc:
        await task_store.finish(task.id, success=False, error="attachment_error")
        await reporter.finalize_text(
            f"تعذر التعامل مع أحد الملفات: {exc}.",
            status="failed",
            message="تعذر تجهيز أحد الملفات.",
        )
        audit.write("task_finished", "attachment_error", actor_id=task.owner_id, details={"task_id": task.id})
    except Exception as exc:
        current = await task_store.get(task.id)
        if current is None or current.status != "cancelled":
            await task_store.finish(task.id, success=False, error=type(exc).__name__)
            await reporter.finalize_text(
                user_error(exc, "تنفيذ الطلب"),
                status="failed",
                message="تعذر إكمال الطلب.",
            )
            audit.write(
                "task_finished",
                "error",
                actor_id=task.owner_id,
                details={"task_id": task.id, "error_type": type(exc).__name__},
            )
        else:
            await reporter.finalize_text(
                "تم إلغاء الطلب.",
                status="cancelled",
                message="تم إلغاء الطلب أثناء التنفيذ.",
            )
    finally:
        try:
            attachment_store.cleanup_task_work(task.id)
        except Exception as exc:
            log.warning("تعذر تنظيف مساحة العمل المؤقتة للمهمة %s: %s", task.id, type(exc).__name__)
        if task.attachments and not task.is_recurring:
            try:
                attachment_store.delete_input_records(task.attachments)
            except Exception as exc:
                log.warning("تعذر تنظيف مرفقات الإدخال للمهمة %s: %s", task.id, type(exc).__name__)
        stop_typing.set()
        if event_task:
            event_task.cancel()
            try:
                await event_task
            except asyncio.CancelledError:
                pass
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
        live_reporters.pop(task.id, None)


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
    try:
        if not MAINTENANCE_REPORT_PATH.is_file():
            await _safe_reply(update.message, "لسّا ما في تقرير صيانة يومي. أول تقرير بينحفظ بعد أول تشغيل مجدول.")
            return
        report = MAINTENANCE_REPORT_PATH.read_text(encoding="utf-8").strip()
        if not report:
            await _safe_reply(update.message, "تقرير الصيانة الحالي فاضي. راجع سجل خدمة الصيانة.")
            return
        await _safe_reply(update.message, report[:3500] + ("\n… تم اختصار التقرير." if len(report) > 3500 else ""))
    except Exception as exc:
        log.exception("فشل عرض تقرير الصيانة")
        await _safe_reply(update.message, user_error(exc, "عرض تقرير الصيانة"))


@authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _safe_reply(update.message, HELP_TEXT)


@authorized
async def cmd_research_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().research(update, context)

@authorized
async def cmd_discard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _media_adapter().discard(update, context)


@authorized
async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _media_adapter().handle_attachment(update, context)


@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _task_command_adapter().text(update, context)

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
        await model_manager.start()
    try:
        health = await client.health()
        if health.get("healthy"):
            log.info("وكيل OpenCode متاح (الإصدار %s).", health.get("version", "غير معروف"))
        else:
            log.warning("وكيل OpenCode استجاب دون حالة سليمة.")
    except Exception as exc:
        log.warning("وكيل OpenCode غير متاح عند بدء التشغيل: %s", exc)

    commands = [
        BotCommand("start", "بدء جلسة جديدة"),
        BotCommand("new", "إنشاء جلسة جديدة"),
        BotCommand("reset", "إعادة ضبط الجلسة"),
        BotCommand("abort", "إيقاف الطلب الجاري"),
        BotCommand("stop", "إيقاف الطلب الجاري"),
        BotCommand("tasks", "عرض الطلبات الحالية"),
        BotCommand("progress", "عرض تقدم الطلب الحالي"),
        BotCommand("trace", "عرض سجل الطلب الحالي"),
        BotCommand("cancel", "إلغاء الطلب الحالي"),
        BotCommand("discard", "حذف المرفقات المعلّقة"),
        BotCommand("schedule", "إنشاء مهمة مجدولة باسم"),
        BotCommand("repeat", "إنشاء مهمة متكررة باسم"),
        BotCommand("schedules", "عرض المهام المجدولة"),
        BotCommand("schedshow", "عرض تفاصيل مهمة مجدولة"),
        BotCommand("schedrename", "تغيير اسم مهمة مجدولة"),
        BotCommand("schededit", "استبدال أمر مهمة مجدولة"),
        BotCommand("schedappend", "إلحاق نص بأمر مجدول"),
        BotCommand("schedtime", "تغيير وقت التشغيل التالي"),
        BotCommand("schedinterval", "تغيير فترة التكرار"),
        BotCommand("schedrun", "تشغيل مهمة مجدولة الآن"),
        BotCommand("schedpause", "إيقاف مهمة مجدولة"),
        BotCommand("schedresume", "تشغيل مهمة مجدولة"),
        BotCommand("scheddelete", "حذف مهمة مجدولة"),
        BotCommand("model", "عرض النموذج التلقائي وترتيبه"),
        BotCommand("status", "عرض حالة الجلسة"),
        BotCommand("health", "فحص اتصال الوكيل"),
        BotCommand("agents", "عرض الوكلاء المتاحين"),
        BotCommand("maintenance", "عرض آخر تقرير صيانة"),
        BotCommand("search", "بحث موثّق سريع"),
        BotCommand("deepresearch", "بحث عميق متعدد المصادر"),
        BotCommand("extreme", "بحث شديد العمق"),
        BotCommand("news", "بحث إخباري حديث"),
        BotCommand("compare", "مقارنة موثّقة"),
        BotCommand("factcheck", "تدقيق ادعاء"),
        BotCommand("verify", "تحقق من معلومة"),
        BotCommand("open", "فحص رابط أو مصدر"),
        BotCommand("extract", "استخراج بيانات"),
        BotCommand("share", "إنشاء رابط مشاركة"),
        BotCommand("unshare", "إلغاء رابط المشاركة"),
        BotCommand("help", "المساعدة"),
    ]
    await app.bot.set_my_commands(commands)
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
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .get_updates_request(updates_request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("reset", cmd_new))
    app.add_handler(CommandHandler("abort", cmd_abort))
    app.add_handler(CommandHandler("stop", cmd_abort))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("trace", cmd_trace))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("discard", cmd_discard))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("repeat", cmd_repeat))
    app.add_handler(CommandHandler("schedules", cmd_schedules))
    app.add_handler(CommandHandler("schedshow", cmd_schedshow))
    app.add_handler(CommandHandler("schedrename", cmd_schedrename))
    app.add_handler(CommandHandler("schededit", cmd_schededit))
    app.add_handler(CommandHandler("schedappend", cmd_schedappend))
    app.add_handler(CommandHandler("schedtime", cmd_schedtime))
    app.add_handler(CommandHandler("schedinterval", cmd_schedinterval))
    app.add_handler(CommandHandler("schedrun", cmd_schedrun))
    app.add_handler(CommandHandler("schedpause", cmd_schedpause))
    app.add_handler(CommandHandler("schedresume", cmd_schedresume))
    app.add_handler(CommandHandler("scheddelete", cmd_scheddelete))
    app.add_handler(CommandHandler("share", cmd_share))
    app.add_handler(CommandHandler("unshare", cmd_unshare))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(CommandHandler(tuple(RESEARCH_COMMAND_MODES), cmd_research_mode))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_reboot_callback, pattern=r"^reboot:(now|cancel)$"))
    app.add_handler(MessageHandler(filters.ATTACHMENT, handle_attachment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

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
