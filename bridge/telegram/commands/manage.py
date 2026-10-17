"""Full management-center text commands: mirror / prompt / daily / task detail."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.prompt_files import is_prompt_filename

Reply = Callable[[Any, str], Awaitable[None]]
CreateStatus = Callable[[Any, int, str], Awaitable[int | None]]
EditStatus = Callable[[Any, int, int | None, str], Awaitable[None]]
WakeTasks = Callable[[], None]
ErrorMessage = Callable[[Exception, str], str]
AuditWrite = Callable[..., None]


def _owner(update: Update) -> str:
    if update.effective_user is None:
        raise ValueError("تعذر تحديد المستخدم")
    return str(update.effective_user.id)


def _chat(update: Update) -> int:
    if update.effective_chat is None:
        raise ValueError("تعذر تحديد المحادثة")
    return int(update.effective_chat.id)


class ManageCommands:
    def __init__(
        self,
        task_service: Any,
        schedule_service: Any,
        downloads: Any,
        mirrors: Any,
        attachment_store: Any,
        *,
        reply: Reply,
        create_status: CreateStatus,
        edit_status: EditStatus,
        wake_tasks: WakeTasks,
        error_message: ErrorMessage,
        audit_write: AuditWrite,
        logger: logging.Logger | None = None,
    ) -> None:
        self.tasks = task_service
        self.schedules = schedule_service
        self.downloads = downloads
        self.mirrors = mirrors
        self.attachments = attachment_store
        self.reply = reply
        self.create_status = create_status
        self.edit_status = edit_status
        self.wake_tasks = wake_tasks
        self.error_message = error_message
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------- /mirror
    async def mirror(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = list(context.args or [])
        if not args:
            await self.reply(
                update.message,
                "الاستخدام:\n"
                "/mirror <رابط> [catbox|tempfile] — نسخ رابط إلى مرآة\n"
                "/mirror file <رقم-الملف> [gofile|pixeldrain|transfersh|zerox0|catbox|tmpfiles] — رفع ملف منزّل\n"
                "/mirror big <رقم-الملف> — تقسيم ملف 4GB+ ورفع أجزائه تلقائيًا\n"
                "بدون خيارات: يُرفع أول ملف بأصغر مرآة تتسع له تلقائيًا.",
            )
            return
        owner = _owner(update)
        if args[0] == "file" and len(args) >= 2:
            await self._mirror_file(update, owner, args[1], args[2] if len(args) > 2 else "auto")
            return
        if args[0] == "big" and len(args) >= 2:
            await self._mirror_big(update, owner, args[1])
            return
        if args[0].startswith("http"):
            provider = args[1] if len(args) > 1 else "catbox"
            status = await update.message.reply_text("☁️ جارٍ النسخ إلى المرآة…")
            try:
                link = await self.mirrors.mirror_url(provider, args[0])
                await status.edit_text(f"✅ تم النسخ ({provider}):\n{link}")
                self.audit_write("mirror_url", "completed", actor_id=owner,
                                 details={"provider": provider})
            except Exception as exc:
                self.log.warning("فشل نسخ الرابط إلى مرآة: %s", type(exc).__name__)
                await status.edit_text(f"❌ تعذر النسخ: {exc}")
            return
        await self._mirror_file(update, owner, args[0], args[1] if len(args) > 1 else "auto")

    async def _records(self, owner: str) -> list[Any]:
        try:
            return list(self.downloads.list(owner))
        except Exception:
            return []

    async def _mirror_file(self, update: Update, owner: str, index: str, provider: str) -> None:
        records = await self._records(owner)
        # Also expose task outputs as mirrorable files (absolute store root).
        extra: list[tuple[str, Path]] = []
        try:
            outgoing = getattr(self.attachments, "outgoing_root", None)
            outgoing_path = Path(str(outgoing)) if outgoing else Path("runtime/attachments/outgoing")
            if outgoing_path.is_dir():
                for task_dir in sorted(outgoing_path.iterdir()):
                    if task_dir.is_dir():
                        for item in sorted(task_dir.rglob("*")):
                            if item.is_file() and not item.is_symlink() and item.stat().st_size > 0:
                                extra.append((f"{task_dir.name}/{item.name}", item))
                                if len(extra) >= 8:
                                    break
        except OSError:
            pass
        try:
            idx = int(index)
        except ValueError:
            await self.reply(update.message, "رقم الملف يجب أن يكون رقمًا من قائمة /menu » الملفات.")
            return
        status = await update.message.reply_text("☁️ جارٍ الرفع إلى المرآة…")
        try:
            if idx < len(records):
                target = Path(records[idx].path)
                label = records[idx].filename
            elif 0 <= idx - len(records) < len(extra):
                label, target = extra[idx - len(records)]
            else:
                await status.edit_text("رقم الملف خارج القائمة. افتح /menu » الملفات لعرض الأرقام.")
                return
            if provider == "auto":
                used, url = await self.mirrors.mirror_auto(target)
                await status.edit_text(f"✅ تم الرفع ({used}):\n{url}")
            else:
                url = await self.mirrors.mirror_file(provider, target)
                await status.edit_text(f"✅ تم الرفع ({provider}):\n{url}")
            self.audit_write("mirror_file", "completed", actor_id=owner,
                             details={"provider": provider, "file": label})
        except Exception as exc:
            self.log.warning("فشل رفع الملف إلى مرآة: %s", type(exc).__name__)
            await status.edit_text(f"❌ تعذر الرفع: {exc}")

    async def _mirror_big(self, update: Update, owner: str, index: str) -> None:
        records = await self._records(owner)
        try:
            idx = int(index)
            target = Path(records[idx].path)
        except (ValueError, IndexError):
            await self.reply(update.message, "الاستخدام: /mirror big <رقم-الملف>")
            return
        status = await update.message.reply_text("☁️ جارٍ تقسيم الملف الكبير ورفع أجزائه… قد يستغرق دقائق.")
        try:
            urls = await self.mirrors.mirror_large_file(target)
            body = "\n".join(urls)[:3500]
            await status.edit_text(
                f"✅ رُفع الملف على {len(urls)} أجزاء (ادمجها بالترتيب بعد التنزيل):\n{body}"
            )
            self.audit_write("mirror_big", "completed", actor_id=owner,
                             details={"parts": len(urls)})
        except Exception as exc:
            await status.edit_text(f"❌ تعذر رفع الملف الكبير: {exc}")

    # ------------------------------------------------------------- /prompt
    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Queue a long prompt file's instructions: /prompt <text> [+ attached .md]."""
        text = " ".join(context.args or []).strip()
        if not text:
            await self.reply(
                update.message,
                "الاستخدام: أرفق ملف .md ثم اكتب /prompt نفّذ الملف\n"
                "أو: /prompt <الأمر الطويل مباشرة>\n"
                "يُنفَّذ محتوى ملف البرومبت حرفيًا مع نصك.",
            )
            return
        owner, chat = _owner(update), _chat(update)
        status_id = await self.create_status(context.bot, chat, "جاري تجهيز البرومبت…")
        try:
            queued = await self.tasks.enqueue_prompt(
                owner, chat, text,
                status_message_id=status_id,
                direct_dispatch=True,
                idempotency_scope="telegram_update",
                idempotency_key=f"prompt-{update.update_id}",
            )
            self.wake_tasks()
            self.audit_write("prompt_queued", "accepted", actor_id=owner,
                             details={"task_id": queued.task.id, "length": len(text)})
        except Exception as exc:
            self.log.exception("فشل تسجيل البرومبت")
            await self.reply(update.message, self.error_message(exc, "تسجيل البرومبت"))

    # ------------------------------------------------------------- /taskshow
    async def taskshow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await self.reply(update.message, "الاستخدام: /taskshow <رقم-المهمة>")
            return
        owner = _owner(update)
        try:
            task_id = int(context.args[0])
        except ValueError:
            await self.reply(update.message, "رقم المهمة يجب أن يكون رقمًا.")
            return
        task = await self.tasks.repository.get(task_id)
        if task is None or str(getattr(task, "owner_id", "")) != owner:
            # Fall back to public id lookup across active/failed.
            found = None
            for pool in (await self.tasks.active(owner), await self.tasks.failed(owner, 100)):
                for item in pool:
                    if str(item.id) == context.args[0] or getattr(item, "public_id", "") == context.args[0]:
                        found = item
                        break
            task = found
        if task is None:
            await self.reply(update.message, "لم يتم العثور على المهمة.")
            return
        checkpoint = dict(getattr(task, "checkpoint", None) or {})
        lines = [
            f"📋 مهمة #{task.id}",
            f"الحالة: {task.status}",
            f"الأمر: {(task.prompt or '')[:600]}",
            f"المرفقات: {len(getattr(task, 'attachments', None) or [])}",
            f"مباشرة: {'نعم' if checkpoint.get('direct_dispatch') else 'لا'}",
            f"يومية: {'نعم' if checkpoint.get('daily_continuation') else 'لا'}",
            f"تكرار الاستكمال: {checkpoint.get('continuation_count', 0)}",
            f"الجلسة: {checkpoint.get('session_id', '—')}",
            f"النموذج: {checkpoint.get('model', '—')}",
            f"آخر خطأ: {getattr(task, 'last_error', None) or '—'}",
        ]
        await self.reply(update.message, "\n".join(lines)[:4000])

    # ------------------------------------------------------------- /dailies
    async def dailies(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List + manage durable daily tasks: /dailies [done <id>|cancel <id>]."""
        owner = _owner(update)
        active = await self.tasks.active(owner)
        daily = [t for t in active if (dict(getattr(t, "checkpoint", None) or {}).get("daily_continuation"))]
        if context.args and len(context.args) >= 2 and context.args[0] in {"done", "cancel"}:
            try:
                task_id = int(context.args[1])
            except ValueError:
                await self.reply(update.message, "رقم المهمة يجب أن يكون رقمًا.")
                return
            if context.args[0] == "cancel":
                result = await self.tasks.cancel(task_id, owner)
                self.wake_tasks()
                await self.reply(update.message, "تم إلغاء المهمة اليومية." if result else "لم يتم العثور عليها.")
                return
            # done: force-complete by clearing the continuation flag then finishing.
            task = await self.tasks.repository.get(task_id)
            if task is None or str(task.owner_id) != owner:
                await self.reply(update.message, "لم يتم العثور على المهمة اليومية.")
                return
            checkpoint = dict(getattr(task, "checkpoint", None) or {})
            checkpoint.pop("daily_continuation", None)
            await self.tasks.repository.save_checkpoint(task.id, checkpoint)
            await self.tasks.repository.finish(task.id, success=True)
            self.audit_write("daily_done", "completed", actor_id=owner, details={"task_id": task_id})
            await self.reply(update.message, f"تم إنهاء المهمة اليومية #{task_id} ولن تُستأنف.")
            return
        if not daily:
            await self.reply(update.message, "لا توجد مهام يومية نشطة. أنشئ واحدة بـ /daily الأمر أو من لوحة /menu.")
            return
        lines = ["🔁 المهام اليومية النشطة:"]
        for task in daily[:20]:
            checkpoint = dict(getattr(task, "checkpoint", None) or {})
            count = checkpoint.get("continuation_count", 0)
            lines.append(f"• #{task.id} — {task.status} — استكمال #{count} — {(task.prompt or '')[:80]}")
        lines.append("للإنهاء: /dailies done <الرقم> — للإلغاء: /dailies cancel <الرقم>")
        await self.reply(update.message, "\n".join(lines)[:4000])

    # ------------------------------------------------------------- /topics
    async def topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Queue a news/topics digest with rich delivery (photos + open-page buttons)."""
        from prompt_enhancer import ResearchMode

        text = " ".join(context.args or []).strip()
        if not text:
            await self.reply(update.message, "الاستخدام: /topics <الموضوع> — مثال: /topics أخبار الذكاء الاصطناعي اليوم")
            return
        owner, chat = _owner(update), _chat(update)
        status_id = await self.create_status(context.bot, chat, "جاري تجهيز المواضيع…")
        try:
            queued = await self.tasks.enqueue_prompt(
                owner, chat, text,
                status_message_id=status_id,
                execution_mode=ResearchMode.NEWS,
                idempotency_scope="telegram_update",
                idempotency_key=f"topics-{update.update_id}",
            )
            self.wake_tasks()
            self.audit_write("topics_queued", "accepted", actor_id=owner,
                             details={"task_id": queued.task.id})
        except Exception as exc:
            self.log.exception("فشل تسجيل المواضيع")
            await self.reply(update.message, self.error_message(exc, "تسجيل المواضيع"))

    # ------------------------------------------------- prompt-file helper
    @staticmethod
    def describe_prompt_files(records: list[dict]) -> list[str]:
        names: list[str] = []
        for record in records:
            filename = str(record.get("filename", ""))
            if is_prompt_filename(filename, str(record.get("mime", ""))):
                names.append(filename)
        return names
