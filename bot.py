"""Arabic Telegram bridge for a locally hosted OpenCode agent."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from telegram import BotCommand, Update
from telegram.constants import ChatAction, ChatType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

BRIDGE_DIR = Path(__file__).parent
MAINTENANCE_REPORT_PATH = BRIDGE_DIR / "runtime" / "maintenance-latest.md"
ATTACHMENT_ROOT = BRIDGE_DIR / "runtime" / "attachments"
sys.path.insert(0, str(BRIDGE_DIR))

from attachments import AttachmentError, AttachmentStore, attachment_prompt_note
from audit_log import AuditLogger
from block_patterns import check_build, check_hardline
from formatter import format_and_chunk
from messages import (
    HELP_TEXT,
    build_blocked_message,
    empty_response_message,
    startup_message,
    unauthorized_message,
    user_error,
)
from opencode_client import OpenCodeClient, extract_file_response, extract_text_response
from prompt_enhancer import enhance_prompt
from session_store import SessionStore
from task_queue import QueuedTask, TaskQueueStore
from task_service import TaskService

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
DEFAULT_MODEL = os.environ.get("OPENCODE_DEFAULT_MODEL", "opencode/big-pickle")
DEFAULT_AGENT = os.environ.get("OPENCODE_AGENT", "telegram-operator")
ATTACHMENT_MAX_BYTES = max(1, int(os.environ.get("TELEGRAM_ATTACHMENT_MAX_BYTES", str(20 * 1024 * 1024))))

store = SessionStore(BRIDGE_DIR / "sessions.db")
task_store = TaskQueueStore(BRIDGE_DIR / "sessions.db")
client = OpenCodeClient(host=OPENCODE_HOST, port=OPENCODE_PORT, password=OPENCODE_PASSWORD)
audit = AuditLogger(BRIDGE_DIR / "runtime" / "audit.jsonl")
attachment_store = AttachmentStore(ATTACHMENT_ROOT, max_bytes=ATTACHMENT_MAX_BYTES)
task_service: TaskService | None = None
UTC = timezone.utc

F = TypeVar("F", bound=Callable[..., Awaitable[None]])


def _is_allowed(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or user.is_bot or user.id not in ALLOWED_USERS:
        return False
    if chat is None:
        return False
    # Private chats are safe by default. Groups require an explicit chat-ID
    # allowlist to prevent an administrator from accidentally exposing control.
    return chat.type == ChatType.PRIVATE or chat.id in ALLOWED_CHAT_IDS


def _extract_session_id(session: dict) -> str:
    session_id = session.get("id") or session.get("sessionId") or session.get("session", {}).get("id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("استجاب الوكيل دون معرّف جلسة صالح")
    return session_id


async def _ensure_session(telegram_user_id: str) -> str:
    current = await store.get_session(telegram_user_id)
    if current:
        return current.opencode_session_id
    created = await client.create_session(title=f"جلسة تيليغرام {telegram_user_id}")
    session_id = _extract_session_id(created)
    await client.update_session(session_id, model=DEFAULT_MODEL)
    await store.create_session(telegram_user_id, session_id, DEFAULT_MODEL)
    return session_id


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


def authorized(handler: F) -> F:
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            if update.message:
                user_id = update.effective_user.id if update.effective_user else "مجهول"
                log.warning("محاولة وصول غير مصرح بها من المستخدم %s", user_id)
                audit.write("access_attempt", "denied", actor_id=user_id, details={"handler": handler.__name__})
                await _safe_reply(update.message, unauthorized_message())
            return
        log.info("المستخدم %s طلب %s", update.effective_user.id, handler.__name__)
        audit.write("handler_invoked", "accepted", actor_id=update.effective_user.id, details={"handler": handler.__name__})
        await handler(update, context)

    return wrapper  # type: ignore[return-value]


async def _create_fresh_session(user_id: str) -> str:
    existing = await store.get_session(user_id)
    if existing:
        try:
            await client.abort_session(existing.opencode_session_id)
        except Exception as exc:
            log.info("تعذر إيقاف الجلسة السابقة قبل الاستبدال: %s", exc)
        await store.delete_session(user_id)
    return await _ensure_session(user_id)


def _parse_utc_datetime(value: str) -> datetime:
    """Parse the documented UTC schedule format without guessing the timezone."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("اكتب الوقت بصيغة UTC: YYYY-MM-DD HH:MM") from exc


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
                caption=f"ملف ناتج عن المهمة #{task.id}" if sent == 0 else None,
            )
        sent += 1
    return sent


async def _execute_agent_task(task: QueuedTask, bot) -> None:
    """Execute one claimed task and deliver text and managed files to its chat."""
    current = await task_store.get(task.id)
    if current is None or current.status == "cancelled":
        return

    await bot.send_message(chat_id=task.chat_id, text=f"بلّشت تنفيذ المهمة #{task.id}. رح أبعتلك النتيجة والملفات أول ما تخلص.")
    audit.write(
        "task_started",
        "accepted",
        actor_id=task.owner_id,
        details={"task_id": task.id, "scheduled": task.is_recurring, "attachment_count": len(task.attachments)},
    )
    stop_typing = asyncio.Event()
    typing_task: asyncio.Task[None] | None = None
    try:
        attachments = attachment_store.validate_input_records(task.attachments)
        output_directory = attachment_store.task_output_directory(task.id)
        enhanced = enhance_prompt(task.prompt)
        prompt = enhanced.text + "\n\n" + attachment_prompt_note(attachments, output_directory)
        message_parts = [attachment.to_message_part() for attachment in attachments]
        session_id = await _ensure_session(task.owner_id)
        typing_task = asyncio.create_task(_typing_loop(task.chat_id, bot, stop_typing))
        started_at = time.monotonic()
        response = await client.send_prompt(session_id, prompt, agent=DEFAULT_AGENT, parts=message_parts)
        reply_text = extract_text_response(response)
        output_files = attachment_store.collect_task_outputs(task.id)
        if not reply_text and not output_files:
            await bot.send_message(chat_id=task.chat_id, text=f"المهمة #{task.id} ما رجّعت نتيجة واضحة؛ عم جرّب مرة أخيرة.")
            response = await client.send_prompt(session_id, prompt, agent=DEFAULT_AGENT, parts=message_parts)
            reply_text = extract_text_response(response)
            output_files = attachment_store.collect_task_outputs(task.id)
        elapsed = time.monotonic() - started_at
        current = await task_store.get(task.id)
        if current is None or current.status == "cancelled":
            await bot.send_message(chat_id=task.chat_id, text=f"تم إلغاء المهمة #{task.id} قبل إرسال النتيجة.")
            audit.write("task_cancelled", "cancelled", actor_id=task.owner_id, details={"task_id": task.id})
            return
        if not reply_text and not output_files:
            await bot.send_message(chat_id=task.chat_id, text=empty_response_message())
            await task_store.finish(task.id, success=False, error="empty_response")
            audit.write(
                "task_finished",
                "empty_response",
                actor_id=task.owner_id,
                details={"task_id": task.id, "duration_seconds": round(elapsed, 2), "agent_file_parts": len(extract_file_response(response))},
            )
            return
        if reply_text:
            for chunk in format_and_chunk(reply_text):
                await bot.send_message(chat_id=task.chat_id, text=chunk, disable_web_page_preview=True)
        delivered_files = await _send_task_output_files(task, bot)
        if delivered_files and not reply_text:
            await bot.send_message(chat_id=task.chat_id, text=f"تم تجهيز وإرسال {delivered_files} ملف من المهمة #{task.id}.")
        await task_store.finish(task.id, success=True)
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
            },
        )
    except AttachmentError as exc:
        await bot.send_message(chat_id=task.chat_id, text=f"تعذّر التعامل مع مرفق المهمة #{task.id}: {exc}.")
        await task_store.finish(task.id, success=False, error="attachment_error")
        audit.write("task_finished", "attachment_error", actor_id=task.owner_id, details={"task_id": task.id})
    except Exception as exc:
        current = await task_store.get(task.id)
        if current is None or current.status != "cancelled":
            await bot.send_message(chat_id=task.chat_id, text=user_error(exc, f"تنفيذ المهمة #{task.id}"))
            await task_store.finish(task.id, success=False, error=type(exc).__name__)
            audit.write("task_finished", "error", actor_id=task.owner_id, details={"task_id": task.id, "error_type": type(exc).__name__})
    finally:
        stop_typing.set()
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass


@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _create_fresh_session(str(update.effective_user.id))
        await _safe_reply(update.message, f"{startup_message()}\n\n{HELP_TEXT}")
    except Exception as exc:
        log.exception("فشل إنشاء جلسة البداية")
        await _safe_reply(update.message, user_error(exc, "إنشاء الجلسة"))


@authorized
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await _create_fresh_session(str(update.effective_user.id))
        await _safe_reply(update.message, "تم إنشاء جلسة جديدة بنجاح. أرسل طلبك للبدء.")
    except Exception as exc:
        log.exception("فشل إنشاء جلسة جديدة")
        await _safe_reply(update.message, user_error(exc, "إنشاء جلسة جديدة"))


@authorized
async def cmd_abort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    try:
        cancelled = await task_store.cancel_running_for_owner(user_id)
        session = await store.get_session(user_id)
        stopped = bool(session and await client.abort_session(session.opencode_session_id))
        if cancelled:
            await _safe_reply(update.message, f"تمام، ألغيت المهمة الجارية #{cancelled.id}." )
            audit.write("task_cancelled", "cancelled", actor_id=user_id, details={"task_id": cancelled.id, "source": "abort"})
        elif stopped:
            await _safe_reply(update.message, "تم إرسال طلب إيقاف الجلسة الحالية.")
        else:
            await _safe_reply(update.message, "ما في مهمة جارية هلّق لإيقافها.")
    except Exception as exc:
        log.exception("فشل إيقاف المهمة")
        await _safe_reply(update.message, user_error(exc, "إيقاف المهمة"))


@authorized
async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tasks = await task_store.list_active(str(update.effective_user.id))
    if not tasks:
        await _safe_reply(update.message, "ما في مهام بانتظار التنفيذ أو مجدولة هلّق.")
        return
    lines = ["المهام الحالية:"]
    for task in tasks:
        timing = ""
        if task.status == "scheduled" and task.due_at:
            timing = f" — موعدها {task.due_at.strftime('%Y-%m-%d %H:%M UTC')}"
        elif task.status == "queued":
            timing = f" — ترتيبها {task.sequence}"
        elif task.status == "running":
            timing = " — عم تنفّذ هلّق"
        repeat = " — متكررة" if task.is_recurring else ""
        preview = task.prompt.replace("\n", " ")[:70]
        lines.append(f"#{task.id} · {_task_status_text(task)}{timing}{repeat}\n{preview}")
    await _safe_reply(update.message, "\n\n".join(lines))


@authorized
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await _safe_reply(update.message, "استخدمها هيك: /cancel رقم_المهمة")
        return
    task_id = int(context.args[0])
    user_id = str(update.effective_user.id)
    try:
        before_cancel = await task_store.get(task_id)
        cancelled = await task_store.cancel(task_id, user_id)
        if not cancelled:
            await _safe_reply(update.message, "ما لقيت مهمة قابلة للإلغاء بهالرقم.")
            return
        if before_cancel and before_cancel.status == "running":
            session = await store.get_session(user_id)
            if session:
                await client.abort_session(session.opencode_session_id)
        await _safe_reply(update.message, f"تمام، ألغيت المهمة #{task_id}.")
        audit.write("task_cancelled", "cancelled", actor_id=user_id, details={"task_id": task_id, "source": "cancel"})
    except Exception as exc:
        log.exception("فشل إلغاء المهمة %s", task_id)
        await _safe_reply(update.message, user_error(exc, "إلغاء المهمة"))


def _split_schedule_args(raw: str) -> tuple[str, str]:
    if "|" not in raw:
        raise ValueError("افصل الوقت عن الطلب بعلامة |")
    left, prompt = (part.strip() for part in raw.split("|", 1))
    if not left or not prompt:
        raise ValueError("اكتب الوقت والطلب بعد علامة |")
    return left, prompt


@authorized
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        when_text, prompt = _split_schedule_args(" ".join(context.args))
        reason = check_build(prompt) or check_hardline(prompt)
        if reason:
            await _safe_reply(update.message, f"ما جدولت الطلب لأنه محظور لحماية الخادم: {reason}.")
            return
        due_at = _parse_utc_datetime(when_text)
        task = await task_store.schedule(str(update.effective_user.id), update.effective_chat.id, prompt, due_at)
        assert task_service is not None
        task_service.wake()
        await _safe_reply(update.message, f"تمام، جدولت المهمة #{task.id} لوقت {due_at.strftime('%Y-%m-%d %H:%M UTC')}." )
        audit.write("task_scheduled", "accepted", actor_id=task.owner_id, details={"task_id": task.id, "repeat_seconds": None})
    except ValueError as exc:
        await _safe_reply(update.message, f"ما قدرت أجدولها: {exc}.\nمثال: /schedule 2026-08-22 09:30 | فحص حالة الخدمات")
    except Exception as exc:
        log.exception("فشل جدولة المهمة")
        await _safe_reply(update.message, user_error(exc, "جدولة المهمة"))


@authorized
async def cmd_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        interval_text, prompt = _split_schedule_args(" ".join(context.args))
        match = re.fullmatch(r"(\d+)([mhd])", interval_text.lower())
        if not match:
            raise ValueError("اكتب التكرار مثل 30m أو 2h أو 1d")
        amount, unit = int(match.group(1)), match.group(2)
        multiplier = {"m": 60, "h": 3600, "d": 86400}[unit]
        repeat_seconds = amount * multiplier
        if repeat_seconds < 300:
            raise ValueError("أقصر تكرار مسموح هو 5m حتى ما يصير إزعاج")
        reason = check_build(prompt) or check_hardline(prompt)
        if reason:
            await _safe_reply(update.message, f"ما جدولت الطلب لأنه محظور لحماية الخادم: {reason}.")
            return
        due_at = datetime.now(UTC) + timedelta(seconds=repeat_seconds)
        task = await task_store.schedule(str(update.effective_user.id), update.effective_chat.id, prompt, due_at, repeat_seconds)
        assert task_service is not None
        task_service.wake()
        await _safe_reply(update.message, f"تمام، المهمة #{task.id} رح تتكرر كل {interval_text} وأول تنفيذ {due_at.strftime('%Y-%m-%d %H:%M UTC')}." )
        audit.write("task_scheduled", "accepted", actor_id=task.owner_id, details={"task_id": task.id, "repeat_seconds": repeat_seconds})
    except ValueError as exc:
        await _safe_reply(update.message, f"ما قدرت أجدولها: {exc}.\nمثال: /repeat 1d | ابعتلي ملخص حالة الخادم")
    except Exception as exc:
        log.exception("فشل جدولة المهمة المتكررة")
        await _safe_reply(update.message, user_error(exc, "جدولة المهمة المتكررة"))


@authorized
async def cmd_share(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        session = await store.get_session(str(update.effective_user.id))
        if not session:
            await _safe_reply(update.message, "لا توجد جلسة نشطة لمشاركتها.")
            return
        url = await client.share_session(session.opencode_session_id)
        await _safe_reply(update.message, f"رابط المشاركة:\n{url}" if url else "تعذر إنشاء رابط مشاركة للجلسة.")
    except Exception as exc:
        log.exception("فشل إنشاء رابط مشاركة")
        await _safe_reply(update.message, user_error(exc, "إنشاء رابط المشاركة"))


@authorized
async def cmd_unshare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        session = await store.get_session(str(update.effective_user.id))
        if not session:
            await _safe_reply(update.message, "لا توجد جلسة نشطة لإلغاء مشاركتها.")
            return
        removed = await client.unshare_session(session.opencode_session_id)
        await _safe_reply(update.message, "تم إلغاء مشاركة الجلسة." if removed else "تعذر إلغاء رابط المشاركة؛ قد لا يكون موجودًا.")
    except Exception as exc:
        log.exception("فشل إلغاء المشاركة")
        await _safe_reply(update.message, user_error(exc, "إلغاء رابط المشاركة"))


@authorized
async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    try:
        if context.args:
            model = context.args[0]
            session_id = await _ensure_session(user_id)
            await client.update_session(session_id, model=model)
            await store.update_session(user_id, model=model)
            await _safe_reply(update.message, f"تم تغيير النموذج إلى: {model}")
            return

        data = await client.list_providers()
        providers = data.get("all", []) if isinstance(data, dict) else data
        models: list[str] = []
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            provider_name = provider.get("id") or provider.get("name") or "مزود غير مسمى"
            raw_models = provider.get("models", {})
            if isinstance(raw_models, dict):
                models.extend(f"{provider_name}/{model_id}" for model_id in raw_models)
            elif isinstance(raw_models, list):
                for model in raw_models:
                    model_id = model.get("id") if isinstance(model, dict) else str(model)
                    models.append(f"{provider_name}/{model_id}")
        if not models:
            await _safe_reply(update.message, "لم يعثر الوكيل على نماذج متاحة حاليًا.")
            return
        listed = "\n".join(f"• {name}" for name in models[:100])
        suffix = "\n… تم اختصار القائمة." if len(models) > 100 else ""
        await _safe_reply(update.message, f"النماذج المتاحة:\n{listed}{suffix}\n\nللتغيير: /model اسم_النموذج")
    except Exception as exc:
        log.exception("فشل التعامل مع أمر النموذج")
        await _safe_reply(update.message, user_error(exc, "عرض أو تغيير النموذج"))


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        session = await store.get_session(str(update.effective_user.id))
        health = await client.health()
        if not session:
            await _safe_reply(
                update.message,
                f"حالة الوكيل: {'متاح' if health.get('healthy') else 'غير متاح'}\nالإصدار: {health.get('version', 'غير معروف')}\nلا توجد جلسة نشطة.",
            )
            return
        states = await client.get_session_status()
        state = states.get(session.opencode_session_id, {}).get("state", "غير معروف")
        text = (
            "معلومات الجلسة\n"
            f"• حالة الوكيل: {'متاح' if health.get('healthy') else 'غير متاح'}\n"
            f"• إصدار الوكيل: {health.get('version', 'غير معروف')}\n"
            f"• المعرّف: {session.opencode_session_id}\n"
            f"• الحالة: {state}\n"
            f"• النموذج: {session.model or DEFAULT_MODEL}\n"
            f"• الوكيل: {DEFAULT_AGENT}\n"
            f"• الإنشاء: {session.created_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        await _safe_reply(update.message, text)
    except Exception as exc:
        log.exception("فشل عرض حالة الجلسة")
        await _safe_reply(update.message, user_error(exc, "عرض حالة الجلسة"))


@authorized
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        health = await client.health()
        if health.get("healthy"):
            await _safe_reply(update.message, f"الوكيل متصل ويعمل بشكل سليم. الإصدار: {health.get('version', 'غير معروف')}")
        else:
            await _safe_reply(update.message, "الوكيل استجاب لكنه لا يعلن حالة سليمة. راجع سجل الخدمة.")
    except Exception as exc:
        log.exception("فشل الفحص الصحي")
        await _safe_reply(update.message, user_error(exc, "فحص حالة الوكيل"))


@authorized
async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        agents = await client.list_agents()
        if not agents:
            await _safe_reply(update.message, "لم يُرجع الوكيل قائمة بالوكلاء المتاحين.")
            return
        lines = []
        for agent in agents[:40]:
            name = agent.get("name") or agent.get("id") or "وكيل غير مسمى"
            mode = agent.get("mode", "غير محدد")
            description = agent.get("description") or ""
            lines.append(f"• {name} ({mode}){': ' + description if description else ''}")
        await _safe_reply(update.message, "الوكلاء المتاحون:\n" + "\n".join(lines))
    except Exception as exc:
        log.exception("فشل عرض الوكلاء")
        await _safe_reply(update.message, user_error(exc, "عرض الوكلاء"))


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
async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    try:
        attachment = await attachment_store.download_from_message(update.message, context.bot, str(update.effective_user.id))
        prompt = (update.message.caption or "").strip() or f"افحص المرفق «{attachment.filename}» ونفّذ المطلوب المناسب له."
        reason = check_build(prompt) or check_hardline(prompt)
        if reason:
            Path(attachment.path).unlink(missing_ok=True)
            await _safe_reply(update.message, build_blocked_message(reason))
            return
        task, position = await task_store.enqueue(
            str(update.effective_user.id),
            update.effective_chat.id,
            prompt,
            attachments=[attachment.to_record()],
        )
        assert task_service is not None
        task_service.wake()
        position_text = "وهي الجاية بالتنفيذ" if position == 1 else f"بترتيب {position}"
        await _safe_reply(update.message, f"تم استلام «{attachment.filename}» وحفظه بأمان. سجلت المهمة #{task.id} {position_text}.")
        audit.write(
            "attachment_received",
            "accepted",
            actor_id=task.owner_id,
            details={"task_id": task.id, "kind": attachment.kind, "mime": attachment.mime, "size": attachment.size},
        )
    except AttachmentError as exc:
        await _safe_reply(update.message, f"ما قدرت أستلم المرفق: {exc}.")
    except Exception as exc:
        log.exception("فشل استلام مرفق تيليغرام")
        await _safe_reply(update.message, user_error(exc, "استلام المرفق"))


@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_chat:
        return
    text = update.message.text.strip()
    if not text:
        return

    enhanced_prompt = enhance_prompt(text)
    audit.write(
        "request_received",
        "accepted",
        actor_id=update.effective_user.id,
        details={"text_length": len(text), "is_arabic": enhanced_prompt.is_arabic, "is_research": enhanced_prompt.is_research},
    )
    build_reason = check_build(text)
    if build_reason:
        audit.write("request_blocked", "blocked", actor_id=update.effective_user.id, details={"policy": "build", "reason": build_reason})
        await _safe_reply(update.message, build_blocked_message(build_reason))
        return
    hardline_reason = check_hardline(text)
    if hardline_reason:
        audit.write("request_blocked", "blocked", actor_id=update.effective_user.id, details={"policy": "hardline", "reason": hardline_reason})
        await _safe_reply(update.message, f"لم يُنفذ الطلب لأنه محظور لحماية الخادم: {hardline_reason}.")
        return

    task, position = await task_store.enqueue(str(update.effective_user.id), update.effective_chat.id, text)
    assert task_service is not None
    task_service.wake()
    if position == 1:
        reply = f"تمام، سجلت المهمة #{task.id} وهي الجاية بالتنفيذ."
    else:
        reply = f"تمام، سجلت المهمة #{task.id} بترتيب {position}. أول ما تخلص المهمة اللي قبلها رح تبلّش لحالها."
    await _safe_reply(update.message, reply)
    audit.write("task_queued", "accepted", actor_id=task.owner_id, details={"task_id": task.id, "position": position})


async def post_init(app: Application) -> None:
    global task_service
    attachment_store.ensure_directories()
    if task_service is None:
        await task_store.init()
        task_service = TaskService(task_store, lambda task: _execute_agent_task(task, app.bot))
        interrupted = await task_service.start()
        if interrupted:
            audit.write("task_recovery", "interrupted", details={"count": interrupted})
            log.warning("تم تعليم %s مهمة كفاشلة بعد انقطاع سابق.", interrupted)
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
        BotCommand("abort", "إيقاف المهمة الجارية"),
        BotCommand("stop", "إيقاف المهمة الجارية"),
        BotCommand("tasks", "عرض الطابور والمهام المجدولة"),
        BotCommand("cancel", "إلغاء مهمة برقمها"),
        BotCommand("schedule", "جدولة مهمة لوقت UTC"),
        BotCommand("repeat", "جدولة مهمة متكررة"),
        BotCommand("model", "عرض أو تغيير النموذج"),
        BotCommand("status", "عرض حالة الجلسة"),
        BotCommand("health", "فحص اتصال الوكيل"),
        BotCommand("agents", "عرض الوكلاء المتاحين"),
        BotCommand("maintenance", "عرض آخر تقرير صيانة"),
        BotCommand("share", "إنشاء رابط مشاركة"),
        BotCommand("unshare", "إلغاء رابط المشاركة"),
        BotCommand("help", "المساعدة"),
    ]
    await app.bot.set_my_commands(commands)
    log.info(
        "تم تسجيل أوامر البوت. النموذج: %s، الوكيل: %s، وكيل تيليغرام: %s",
        DEFAULT_MODEL,
        DEFAULT_AGENT,
        "مفعّل" if TELEGRAM_PROXY_URL else "غير مفعّل",
    )


async def post_shutdown(app: Application) -> None:
    global task_service
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
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("repeat", cmd_repeat))
    app.add_handler(CommandHandler("share", cmd_share))
    app.add_handler(CommandHandler("unshare", cmd_unshare))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(CommandHandler("help", cmd_help))
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
    log.info("البوت يعمل. النموذج: %s، الوكيل: %s", DEFAULT_MODEL, DEFAULT_AGENT)
    await stop_event.wait()
    await app.updater.stop()
    await app.stop()
    await post_shutdown(app)
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
