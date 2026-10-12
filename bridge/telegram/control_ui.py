"""Bilingual, owner-authorized Telegram button control surface.

Inline callback data is short, revalidated on every press and never carries
user-supplied paths, model identifiers or agent names. Input actions use a
one-message state in Telegram user_data; raw text remains a normal prompt
outside that state.
"""

from __future__ import annotations

import asyncio
import html
import logging
from types import SimpleNamespace
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from bridge.domain.schedules.user_timezones import (
    COUNTRIES,
    display_local,
    local_to_utc,
    validate_zone,
)
from bridge.services.host_credentials import LABEL_RE
from bridge.services.server_admin import update_now
from bridge.telegram.download_delivery import deliver_download

log = logging.getLogger(__name__)

WORDS = {
    "ar": {
        "home": "🏠 الرئيسية", "agent": "🤖 إدارة الوكيل", "tasks": "📋 المهام",
        "schedules": "📅 الجدولة", "downloads": "📁 الملفات", "settings": "⚙️ الإعدادات", "keys": "🔑 مفاتيح API", "server": "🖥 الخادم", "memory": "💾 الذاكرة", "updates": "🔃 التحديث التلقائي", "key_add": "➕ إضافة مفتاح", "key_auto": "🔄 تدوير تلقائي",
        "language": "🌐 اللغة", "models": "🧠 النموذج والتفكير",
        "agents": "🔄 تبديل الوكيل", "workers": "⚡ المهام المتزامنة",
        "direct": "✉️ أمر مباشر", "daily": "🔁 مهمة يومية",
        "research": "🔍 بحث معمّق", "help": "❔ المساعدة", "advanced": "🧰 أدوات متقدمة",
        "back": "⬅️ رجوع", "refresh": "🔄 تحديث", "auto": "تلقائي",
        "stop": "🛑 إيقاف", "retry": "♻️ إعادة المحاولة",
        "yes": "✅ تأكيد", "no": "❌ إلغاء", "delete": "🗑 حذف",
        "on": "✅ مفعّل", "off": "⏸ متوقف",
        "empty": "لا توجد عناصر.", "cancelled": "أُلغي الطلب.",
        "error": "تعذر تنفيذ العملية. حاول من جديد.",
        "input": "أرسل النص في رسالتك التالية (لا يلزم كتابة أمر /).",
        "session": "🆕 جلسة جديدة", "create_schedule": "➕ جدولة جديدة",
        "create_repeat": "🔁 مهمة متكررة", "research_input": "أرسل موضوع البحث المعمّق.",
        "schedule_name": "أرسل اسمًا فريدًا للجدولة.",
        "schedule_time": "أرسل الوقت المحلي مثل 2026-10-15 12:00.",
        "timezone": "🌍 البلد والتوقيت", "tz_custom": "✍️ توقيت آخر",
        "tz_prompt": "أرسل منطقة زمنية IANA مثل Asia/Damascus أو Europe/Berlin.",
        "priority_up": "⬆️ رفع الأولوية", "priority_down": "⬇️ خفض الأولوية",
        "remove_task": "🗑 حذف المهمة", "recent_tasks": "مهام سابقة:",
        "schedule_interval": "أرسل الفاصل الزمني مثل 1d أو 12h أو 30m.",
        "schedule_prompt": "أرسل تعليمات المهمة المجدولة.",
        "download_input": "أرسل رابط HTTPS أو HTTP لتنزيل الملف.",
        "media_input": "أرسل رابط صفحة الفيديو أو الصوت؛ يدعم yt-dlp المواقع التي تتوفر لها مستخرجات.",
        "media_download": "🎬 تحميل فيديو أو صوت", "direct_download": "🌐 ملف مباشر",
        "key_provider": "أرسل معرّف المزوّد مثل openai أو anthropic.",
        "key_label": "أرسل اسمًا للمفتاح (حروف إنجليزية وأرقام فقط).",
        "key_secret": "أرسل قيمة API Key الآن. ستُحذف الرسالة مباشرة.",
        "hostkeys": "🔐 مفاتيح GitHub / Telegram / OpenCode",
        "credentials": "🔐 المفاتيح والاعتمادات",
        "credentials_text": "إدارة موحّدة لمفاتيح الذكاء الاصطناعي واعتمادات الخدمات.",
        "hostadd": "➕ إضافة اعتماد", "hostreplace": "✏️ استبدال اعتماد",
        "host_scope": "اختر الخدمة:", "host_label": "أرسل اسم الاعتماد.",
        "host_value": "أرسل التوكن في رسالة خاصة؛ سنحاول حذف الرسالة.",
        "restart_required": "تفعيل توكن الخدمة سيصبح نافذًا بعد إعادة تشغيل خدمتها. لن يُعرض التوكن نفسه.",
        "backups": "🗄 النسخ الاحتياطية", "backup_create": "➕ نسخة جديدة",
        "backup_send": "📤 إرسال", "backup_delete": "🗑 حذف نسخة",
        "backup_warning": "نسخ قاعدة البيانات غير المشفرة قد تحتوي نصوص مهام حساسة. إرسال المفاتيح مسموح فقط في نسخة مشفرة.",
        "backup_keys": "🔒 مع المفاتيح (مشفرة فقط)", "backup_recipient": "🔐 مفتاح تشفير النسخ الاحتياطية",
        "mirrors": "☁️ مرايا مجانية", "mirror_url": "🔗 نسخ رابط إلى مرآة",
        "mirror_input": "أرسل رابطًا عامًا لمشاركة نسخه عبر المزود الخارجي.",
        "resources": "📊 موارد الخادم", "update_agent": "🤖 تحديث OpenCode الآن",
        "update_system": "🛠 تحديث النظام الآن", "auto_system": "🔄 تحديث النظام تلقائيًا",
        "bridge_check": "🔎 فحص تحديث Bridge الآن (دون تثبيت)",
        "bridge_update": "🚀 تحديث Bridge الآن (بعد التأكيد)",
        "backup_policy": "🗑 حذف النسخ الاحتياطية بعد تحديث ناجح",
        "admin_private": "تتطلب هذه العملية محادثة خاصة مع مالك البوت.",
        "pending_update": "بدأ فحص التحديث. سيتم عرض نتيجة العملية هنا.",
        "queued": "أُضيفت المهمة إلى الطابور. رقمها: ",
        "saved": "حُفظ التغيير.", "not_found": "انتهت صلاحية هذا الزر؛ افتح القائمة من جديد.",
        "home_text": "لوحة تحكم OpenCode Bridge\nاختر القسم أو استخدم الأزرار الثابتة أسفل المحادثة.",
        "agent_text": "إدارة وكيل OpenCode\nالوكيل: {agent}\nالنموذج: {model}\nمستوى التفكير: {reason}",
        "tasks_text": "المهام النشطة والفاشلة (صف انتظار واحد لجميع المستخدمين).",
        "schedules_text": "المهام المجدولة؛ شغّل أو أوقف الجدولة بزر واحد.",
        "download_text": "الملفات المحمّلة: إرسال أو حذف الملفات.",
        "settings_text": "اللغة وحدود التزامن. مهمة واحدة افتراضيًا، وبحد أقصى مهمتين.",
        "language_text": "اختر لغة الواجهة:", "worker_text": "الحد الفعلي الحالي: {limit}\nالحد الأعلى: 2. المهام الجارية لا تُلغى عند خفض العدد.",
        "model_text": "اختر النموذج من الفهرس الحي، ثم اختر مستوى التفكير المدعوم.",
        "reason_text": "مستويات التفكير المتاحة للنموذج {model}:",
        "agent_list_text": "اختر الوكيل من قائمة OpenCode الفعلية:",
        "confirm_text": "هل تؤكد تنفيذ العملية؟",
        "help_text": "اختر مهمة مباشرة أو يومية، ثم أرسل تعليماتك كنص. المهمة اليومية تتابع بعد تجدد الرصيد عند 12:00 UTC. من قسم الوكيل يمكنك تغيير الوكيل والنموذج والتفكير. لعرض الأوامر القديمة استخدم /help.",
        "server_text": "إدارة الخادم: التحديثات والذاكرة وحالة النشر.",
        "memory_text": "إعدادات الذاكرة (تُطبّق بواسطة خدمة النظام خلال ساعة تقريبًا).",
        "updates_text": "وضع تحديث Bridge تلقائيًا: {state}",
        "mode_off": "⏸ إيقاف", "mode_notify": "👁 إشعار فقط",
        "mode_safe": "✅ تلقائي آمن", "mode_window": "🕗 نافذة 08–10 UTC",
        "mode_window_help": "نافذة التحديث اليومية: 08:00–10:00 UTC.",
        "quarantine_text": "⚠️ فشل تفعيل إصدار Bridge {tag}؛ توقفت محاولات هذا الإصدار تلقائيًا. سيُجرَّب إصدار أحدث بعد اجتياز الاختبارات.",
        "quarantine_invalid": "⚠️ سجل التحديث الفاشل تالف؛ تم إيقاف التفعيل تلقائيًا لحين معالجة الحالة.",
        "advanced_text": "أزرار لجميع أدوات الإدارة الإضافية؛ اختر الأداة ثم أرسل خياراتها دون /.",
        "advanced_args": "أرسل الخيارات المطلوبة دون /. استخدم | للفصل عند الجدولة.",
        "success": "تمت العملية.", "number_one": "1 — موصى به", "number_two": "2 — عند الحاجة",
    },
    "en": {
        "home": "🏠 Home", "agent": "🤖 Agent manager", "tasks": "📋 Tasks",
        "schedules": "📅 Schedules", "downloads": "📁 Files", "settings": "⚙️ Settings", "keys": "🔑 API keys", "server": "🖥 Server", "memory": "💾 Memory", "updates": "🔃 Auto-update", "key_add": "➕ Add key", "key_auto": "🔄 Auto rotation",
        "language": "🌐 Language", "models": "🧠 Model & reasoning",
        "agents": "🔄 Switch agent", "workers": "⚡ Parallel tasks",
        "direct": "✉️ Direct task", "daily": "🔁 Daily task",
        "research": "🔍 Deep research", "help": "❔ Help", "advanced": "🧰 Advanced tools",
        "back": "⬅️ Back", "refresh": "🔄 Refresh", "auto": "Automatic",
        "stop": "🛑 Cancel", "retry": "♻️ Retry",
        "yes": "✅ Confirm", "no": "❌ Back", "delete": "🗑 Delete",
        "on": "✅ Enabled", "off": "⏸ Paused",
        "empty": "Nothing to show.", "cancelled": "Action cancelled.",
        "error": "Could not complete the action. Please retry.",
        "input": "Send the instruction in your next message (no / command needed).",
        "session": "🆕 New session", "create_schedule": "➕ New schedule",
        "create_repeat": "🔁 Recurring task", "research_input": "Send your deep-research topic.",
        "schedule_name": "Send a unique schedule name.",
        "schedule_time": "Send local date/time, e.g. 2026-10-15 12:00.",
        "timezone": "🌍 Country & time", "tz_custom": "✍️ Other timezone",
        "tz_prompt": "Send an IANA timezone like America/New_York or Europe/Berlin.",
        "priority_up": "⬆️ Higher priority", "priority_down": "⬇️ Lower priority",
        "remove_task": "🗑 Delete task", "recent_tasks": "Previous tasks:",
        "schedule_interval": "Send an interval such as 1d, 12h or 30m.",
        "schedule_prompt": "Send the task instructions to schedule.",
        "download_input": "Send an HTTPS or HTTP URL to download.",
        "media_input": "Send a video/audio page link supported by yt-dlp.",
        "media_download": "🎬 Video/audio downloader", "direct_download": "🌐 Direct file link",
        "key_provider": "Send provider ID, e.g. openai or anthropic.",
        "key_label": "Send key label (letters/digits only).",
        "key_secret": "Send the API key value now. The message will be deleted.",
        "hostkeys": "🔐 GitHub / Telegram / OpenCode credentials",
        "credentials": "🔐 Keys & credentials",
        "credentials_text": "Manage provider API keys and service credentials in one place.",
        "hostadd": "➕ Add credential", "hostreplace": "✏️ Replace credential",
        "host_scope": "Select service:", "host_label": "Send the credential label.",
        "host_value": "Send the token in a private message; we will attempt to delete it.",
        "restart_required": "The selected service must restart before its new token takes effect. Token values are never shown.",
        "backups": "🗄 Backups", "backup_create": "➕ Create backup",
        "backup_send": "📤 Send", "backup_delete": "🗑 Delete backup",
        "backup_warning": "Unencrypted DB snapshots may include sensitive task text. Secret-inclusive backups are encrypted only.",
        "backup_keys": "🔒 With keys (encrypted only)", "backup_recipient": "🔐 Backup encryption public key",
        "mirrors": "☁️ Free mirrors", "mirror_url": "🔗 Mirror a URL",
        "mirror_input": "Send a public URL to copy to an external host.",
        "resources": "📊 Server resources", "update_agent": "🤖 Update OpenCode now",
        "update_system": "🛠 Update OS now", "auto_system": "🔄 Automatic OS updates",
        "bridge_check": "🔎 Check Bridge release now (no install)",
        "bridge_update": "🚀 Update Bridge now (confirm first)",
        "backup_policy": "🗑 Delete backups after successful upgrade",
        "admin_private": "This action requires the bot owner's private chat.",
        "pending_update": "Update check started. The result will appear here.",
        "queued": "Task queued with ID: ", "saved": "Selection saved.",
        "not_found": "This button is stale; reopen the menu.",
        "home_text": "OpenCode Bridge control center\nChoose a section or use the persistent keyboard below.",
        "agent_text": "OpenCode agent manager\nAgent: {agent}\nModel: {model}\nReasoning: {reason}",
        "tasks_text": "Active and failed tasks (one shared work queue).",
        "schedules_text": "Scheduled jobs. Enable or pause using buttons.",
        "download_text": "Downloaded files: send or remove.",
        "settings_text": "Language and concurrency settings. One task by default, two maximum.",
        "language_text": "Choose interface language:",
        "worker_text": "Current effective limit: {limit}\nMaximum: 2. Decreasing does not terminate running jobs.",
        "model_text": "Select a live catalog model, then its supported reasoning level.",
        "reason_text": "Supported reasoning levels for {model}:",
        "agent_list_text": "Choose an agent from the live OpenCode list:",
        "confirm_text": "Confirm this action?",
        "help_text": "Choose Direct task or Daily task, then send plain instructions. Daily tasks resume after the 12:00 UTC quota reset. Use Agent manager to select the agent, model and reasoning. Legacy commands remain available via /help.",
        "server_text": "Server controls: updates, memory and deployment status.",
        "memory_text": "Memory policy (root service applies updates within roughly an hour).",
        "updates_text": "Bridge update mode: {state}",
        "mode_off": "⏸ Off", "mode_notify": "👁 Notify only",
        "mode_safe": "✅ Auto-safe", "mode_window": "🕗 Window 08–10 UTC",
        "mode_window_help": "Daily deployment window: 08:00–10:00 UTC.",
        "quarantine_text": "⚠️ Bridge {tag} failed activation and is quarantined. A newer verified release can be tried automatically.",
        "quarantine_invalid": "⚠️ Corrupt update quarantine state: automatic activation is blocked until repaired.",
        "advanced_text": "Additional administration tools. Choose an action and send arguments without /.",
        "advanced_args": "Send the required arguments without /. Use | for schedules.",
        "success": "Action completed.", "number_one": "1 — recommended", "number_two": "2 — if needed",
    },
}
BUTTON_KEYS = (("home", "agent"), ("credentials", "backups"), ("direct", "tasks"), ("daily", "schedules"), ("downloads", "mirrors"), ("server", "settings"), ("language", "advanced"), ("help",))
BINDINGS = {value: key for lang in WORDS.values() for key in
            ("home", "agent", "credentials", "keys", "hostkeys", "backups", "mirrors", "direct", "daily", "tasks", "schedules", "downloads", "server", "settings", "language", "advanced", "help")
            for value in (lang[key],)}


def callback(action: str, *parts: Any) -> str:
    result = ":".join(("ux", action, *(str(v) for v in parts)))
    if len(result.encode("utf-8")) > 64:
        raise ValueError("callback exceeds Telegram's 64-byte limit")
    return result


def button(label: str, action: str, *parts: Any) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=callback(action, *parts))


def safe(value: object, limit: int = 100) -> str:
    return html.escape(str(value)[:limit], quote=False)


class ControlUI:
    def __init__(self, *, preferences: Any, agents: Any, models: Any,
                 tasks: Any, schedule_store: Any, schedule_service: Any, downloads: Any,
                 capacity: Any, wake: Any, allowed: Any, provider_keys: Any | None = None,
                 runtime_controls: Any | None = None, admin_allowed: Any | None = None,
                 legacy_dispatch: Any | None = None, research_modes: dict | None = None,
                 host_credentials: Any | None = None, backups: Any | None = None,
                 mirrors: Any | None = None, project: Any | None = None):
        self.preferences = preferences
        self.agents = agents
        self.models = models
        self.tasks = tasks
        self.schedule_store = schedule_store
        self.schedule_service = schedule_service
        self.downloads = downloads
        self.capacity = capacity
        self.wake = wake
        self.allowed = allowed
        self.provider_keys = provider_keys
        self.runtime_controls = runtime_controls
        self.admin_allowed = admin_allowed or allowed
        self.legacy_dispatch = legacy_dispatch
        self.research_modes = research_modes or {}
        self.host_credentials = host_credentials
        self.backups = backups
        self.mirrors = mirrors
        self.project = project
        self._running_updates: set[asyncio.Task] = set()

    async def _lang(self, owner: str) -> str:
        value = (await self.preferences.get(owner)).language
        return value if value in WORDS else "en"

    def keyboard(self, language: str) -> ReplyKeyboardMarkup:
        t = WORDS[language]
        return ReplyKeyboardMarkup([[t[k] for k in row] for row in BUTTON_KEYS],
                                   resize_keyboard=True, is_persistent=True,
                                   input_field_placeholder=t["input"][:64])

    async def show(self, update: Any, context: Any, screen: str = "home",
                   *, persistent: bool = False) -> None:
        if not self.allowed(update) or update.effective_user is None:
            return
        owner = str(update.effective_user.id)
        lang = await self._lang(owner)
        sensitive = {"credentials", "keys", "hostkeys", "backups", "mirrors", "server",
                     "resources", "updates", "memory", "workers", "advanced"}
        if screen in sensitive or screen.startswith("advanced."):
            if (not self.admin_allowed(update)
                    or getattr(getattr(update, "effective_chat", None), "type", None) != "private"):
                return
        if persistent:
            await update.effective_message.reply_text(
                WORDS[lang]["home_text"], reply_markup=self.keyboard(lang))
        text, markup = await self.render(owner, screen)
        await update.effective_message.reply_text(
            text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)

    async def render(self, owner: str, screen: str) -> tuple[str, InlineKeyboardMarkup]:
        lang = await self._lang(owner)
        t = WORDS[lang]
        rows: list[list[InlineKeyboardButton]] = []
        def nav(label: str, target: str) -> None:
            rows.append([button(label, "go", target)])

        if screen == "home":
            text = t["home_text"]
            for key in ("agent", "credentials", "direct", "daily", "research", "tasks", "schedules", "downloads", "mirrors", "backups", "server", "settings", "advanced", "help"):
                if key in ("direct", "daily", "research"):
                    rows.append([button(t[key], "input", key)])
                else:
                    nav(t[key], key)
        elif screen == "agent":
            pref = await self.preferences.get(owner)
            text = t["agent_text"].format(
                agent=safe(pref.agent_preference or self.agents.default_agent),
                model=safe(pref.model_preference or t["auto"]),
                reason=safe(pref.model_variant or t["auto"]))
            nav(t["agents"], "agents")
            nav(t["models"], "models")
            rows.append([button(t["session"], "confirm", "new", 0)])
        elif screen == "agents":
            text = t["agent_list_text"]
            live = await self.agents.agents()
            chosen = (await self.preferences.get(owner)).agent_preference or self.agents.default_agent
            for i, item in enumerate(live[:35]):
                name = str(item.get("name", "")) if isinstance(item, dict) else ""
                if name:
                    rows.append([button(("✅ " if chosen == name else "") + name[:45], "agent", i)])
            rows.append([button(("✅ " if not (await self.preferences.get(owner)).agent_preference else "") + t["auto"], "agent", "auto")])
        elif screen in ("models", "reason"):
            view = await self.models.view(owner)
            if screen == "models":
                text = t["model_text"] + "\n" + safe(view.current_model or t["auto"])
                for i, item in enumerate(view.choices[:40]):
                    rows.append([button(("✅ " if item.model_id == view.current_model else "") + item.label[:45], "model", i)])
                rows.append([button(t["auto"], "model", "auto")])
            else:
                text = t["model_text"]
        elif screen.startswith("reason."):
            index = int(screen.split(".", 1)[1])
            view = await self.models.view(owner)
            model = view.model_at(index)
            if model is None:
                raise ValueError("stale model")
            text = t["reason_text"].format(model=safe(model.label))
            rows.append([button(t["auto"], "reason", index, 0)])
            for j, level in enumerate(model.variants, start=1):
                rows.append([button(("✅ " if level == view.current_variant else "") + level, "reason", index, j)])
        elif screen == "timezone":
            pref = await self.preferences.get(owner)
            from datetime import UTC, datetime
            text = t["timezone"] + "\n" + safe(pref.timezone) + "\n" + safe(display_local(datetime.now(UTC), pref.timezone))
            for i, (country, zone) in enumerate(COUNTRIES):
                rows.append([button(("✅ " if zone == pref.timezone else "") + country, "tz", i)])
            rows.append([button(t["tz_custom"], "input", "tz_custom")])
        elif screen == "tasks":
            text = t["tasks_text"]
            active = await self.tasks.active(owner)
            failed = await self.tasks.failed(owner)
            if not active and not failed:
                text += "\n" + t["empty"]
            for task in active[:8]:
                text += "\n• " + safe(task.status) + " — " + safe(task.prompt, 70)
                if task.status in {"queued", "retrying"}:
                    rows.append([button(t["priority_up"], "priority", task.id, 1),
                                 button(t["priority_down"], "priority", task.id, -1)])
                rows.append([button(t["stop"] + " #" + str(task.id), "confirm", "cancel", task.id),
                             button(t["remove_task"], "confirm", "task", task.id)])
            for task in failed[:8]:
                text += "\n• " + safe(task.status) + " — " + safe(task.prompt, 70)
                rows.append([button(t["retry"] + " #" + str(task.id), "retry", task.id)])
            previous = await self.tasks.recent(owner)
            if previous:
                text += "\n" + t["recent_tasks"]
            for task in previous[:8]:
                text += "\n• " + safe(task.status) + " #" + str(task.id)
                rows.append([button(t["remove_task"] + " #" + str(task.id), "confirm", "task", task.id)])
            rows.append([button(t["direct"], "input", "direct"), button(t["daily"], "input", "daily")])
            nav(t["refresh"], "tasks")
        elif screen == "schedules":
            text = t["schedules_text"]
            jobs = await self.schedule_store.list_scheduled_jobs(owner)
            if not jobs:
                text += "\n" + t["empty"]
            for job in jobs[:15]:
                due = display_local(job.next_run_at, job.timezone_name) if job.next_run_at else "—"
                text += "\n• " + safe(job.name, 60) + " (" + safe(due) + ")"
                rows.append([button((t["on"] if job.enabled else t["off"]) + " " + job.name[:25], "schedule", job.id),
                             button(t["delete"], "confirm", "schedule", job.id)])
            rows.append([button(t["create_schedule"], "input", "schedule_once"),
                         button(t["create_repeat"], "input", "schedule_recurring")])
            nav(t["timezone"], "timezone")
            nav(t["refresh"], "schedules")
        elif screen == "downloads":
            text = t["download_text"]
            records = self.downloads.list(owner)
            for i, item in enumerate(records[:12]):
                text += "\n• " + safe(item.filename, 80)
                rows.append([button("📤 " + item.filename[:28], "file", i),
                             button(t["delete"], "confirm", "file", i)])
            rows.append([button(t["media_download"], "input", "media"),
                         button(t["direct_download"], "input", "download")])
            nav(t["refresh"], "downloads")
        elif screen == "keys":
            text = t["keys"] + "\n"
            entries = self.provider_keys.catalog() if self.provider_keys is not None else []
            for i, item in enumerate(entries[:30]):
                text += "\n• " + safe(item["provider"]) + " / " + safe(item["name"]) + (" ✅" if item["active"] else "")
                rows.append([button(("✅ " if item["active"] else "▶️ ") + item["name"], "keysel", i),
                             button(t["delete"], "confirm", "key", i)])
            if not entries:
                text += t["empty"]
            rows.append([button(t["key_add"], "input", "key_add")])
            providers = sorted({item["provider"] for item in entries})
            for i, provider in enumerate(providers[:20]):
                auto = any(item["auto"] for item in entries if item["provider"] == provider)
                rows.append([button(t["key_auto"] + " " + provider + (" ✅" if auto else ""), "keyauto", i)])
        elif screen == "credentials":
            text = t["credentials_text"]
            nav(t["keys"], "keys")
            nav(t["hostkeys"], "hostkeys")
        elif screen == "hostkeys":
            text = t["hostkeys"]
            entries = self.host_credentials.catalog() if self.host_credentials else []
            for i, item in enumerate(entries[:24]):
                text += "\n• " + safe(item["scope"]) + "/" + safe(item["name"])
                text += " ✅" if item["active"] else ""
                if item.get("managed", True):
                    rows.append([button("▶️ " + str(item["name"]), "hostactivate", i),
                                 button(t["hostreplace"], "hostreplace", i),
                                 button(t["delete"], "confirm", "hostkey", i)])
            rows.append([button(t["hostadd"], "hostadd")])
        elif screen == "mirrors":
            text = t["mirrors"] + "\nCatbox • TempFile.org • TmpFiles"
            for provider in ("catbox", "tempfile"):
                rows.append([button(t["mirror_url"] + " · " + provider, "mirrorurl", provider)])
            for i, item in enumerate(self.downloads.list(owner)[:8]):
                text += "\n• " + safe(item.filename)
                rows.append([button("☁ Catbox", "mirrorfile", "catbox", i),
                             button("☁ TmpFiles", "mirrorfile", "tmpfiles", i)])
        elif screen == "backups":
            text = t["backups"] + "\n" + t["backup_warning"]
            entries = self.backups.list() if self.backups else []
            if not entries:
                text += "\n" + t["empty"]
            for i, item in enumerate(entries[:12]):
                text += "\n• " + safe(item["name"]) + " (" + str(item["bytes"]) + " B)"
                rows.append([button(t["backup_send"], "backupsend", i),
                             button(t["backup_delete"], "confirm", "backup", i)])
            rows.append([button(t["backup_create"], "backupcreate")])
            rows.append([button(t["backup_recipient"], "input", "age_recipient")])
        elif screen == "resources":
            from bridge.services.server_admin import resources
            values = resources(self.project)
            def gb(n: Any) -> str:
                return "—" if n is None else f"{n / 1073741824:.2f} GiB"
            text = t["resources"] + "\n" + (
                f"CPU: {values['logical_cpus']} • Load: {values['cpu_load_1m']}\n"
                f"RAM: {gb(values['memory_available'])} / {gb(values['memory_total'])}\n"
                f"Swap: {gb(values['swap_free'])} / {gb(values['swap_total'])}\n"
                f"Disk free: {gb(values['disk_free'])} / {gb(values['disk_total'])}\n"
                f"Uptime: {values['uptime_seconds'] // 3600} hours"
            )
            nav(t["refresh"], "resources")
        elif screen == "server":
            text = t["server_text"]
            nav(t["updates"], "updates")
            nav(t["memory"], "memory")
            nav(t["workers"], "workers")
            nav(t["mirrors"], "mirrors")
            nav(t["resources"], "resources")
            nav(t["backups"], "backups")
        elif screen == "updates":
            current_mode = (self.runtime_controls.autopilot_mode()
                            if self.runtime_controls else "off")
            names = {"off": "mode_off", "notify": "mode_notify",
                     "auto-safe": "mode_safe", "maintenance-window": "mode_window"}
            text = t["updates_text"].format(state=t[names[current_mode]])
            if current_mode == "maintenance-window":
                text += "\n" + t["mode_window_help"]
            if self.runtime_controls is not None:
                try:
                    held_tag = self.runtime_controls.failed_update_release()
                except (ValueError, OSError):
                    text += "\n" + t["quarantine_invalid"]
                else:
                    if held_tag:
                        text += "\n" + t["quarantine_text"].format(tag=held_tag)
            for value, label in (("off", "mode_off"), ("notify", "mode_notify"),
                                 ("auto-safe", "mode_safe"), ("maintenance-window", "mode_window")):
                rows.append([button(("● " if current_mode == value else "○ ") + t[label],
                                    "autopilot", value)])
            os_enabled = self.runtime_controls.system_auto_update()
            text += "\nOS: " + (t["on"] if os_enabled else t["off"])
            rows.append([button(t["auto_system"], "toggleos")])
            rows.append([button(t["bridge_check"], "checkbridge")])
            rows.append([button(t["bridge_update"], "confirm", "updatebridge", 0)])
            rows.append([button(t["update_agent"], "confirm", "updateagent", 0)])
            rows.append([button(t["update_system"], "confirm", "updatesystem", 0)])
            rows.append([button(t["backup_policy"] + (
                " ✅" if self.runtime_controls.purge_backups_after_update() else ""),
                "togglebackuppurge")])
        elif screen == "memory":
            value = self.runtime_controls.memory() if self.runtime_controls else {"zram_percent": 50, "swap_gib": "auto"}
            text = t["memory_text"] + f"\nZRAM: {value['zram_percent']}% RAM\nSwap: {value['swap_gib']} GiB"
            for percent in ("auto", 25, 50, 75, 100):
                label = f"ZRAM {percent}%" if percent != "auto" else "ZRAM Auto"
                rows.append([button(("✅ " if value["zram_percent"] == percent else "") + label, "zram", percent)])
            for amount in ("auto", 0, 1, 2, 4):
                rows.append([button(("✅ " if value["swap_gib"] == amount else "") + f"Swap {amount} GiB", "swap", amount)])
        elif screen == "settings":
            text = t["settings_text"]
            nav(t["language"], "language")
            nav(t["timezone"], "timezone")
            nav(t["workers"], "workers")
            nav(t["agent"], "agent")
            nav(t["keys"], "keys")
            nav(t["server"], "server")
            nav(t["backups"], "backups")
            nav(t["credentials"], "credentials")
        elif screen == "language":
            text = t["language_text"]
            rows.extend([[button("🇸🇦 العربية", "lang", "ar")],
                         [button("🇬🇧 English", "lang", "en")]])
        elif screen == "workers":
            limit = self.capacity.get()
            text = t["worker_text"].format(limit=limit)
            for n, key in ((1, "number_one"), (2, "number_two")):
                rows.append([button(("✅ " if limit == n else "") + t[key], "workers", n)])
        elif screen == "advanced":
            text = t["advanced_text"]
            for area in ("session", "tasks", "schedules", "research", "other"):
                nav("🗂 " + area, "advanced." + area)
        elif screen.startswith("advanced."):
            area = screen.split(".", 1)[1]
            sections = {"session":["new","abort","share","unshare","status","health","agents","model"],"tasks":["tasks","failed","progress","trace","cancel","retry","discard"],"schedules":["schedules","schedule","repeat","schedshow","schedhistory","schedrename","schededit","schedappend","schedtime","schedinterval","schedtimezone","schedrun","schedpause","schedresume","scheddelete"],"other":["draft","config","limits","maintenance","download","deletefile"],"research": list(self.research_modes)}
            if area not in sections:
                raise ValueError("Invalid advanced category")
            text = t["advanced_text"] + "\n" + area
            for name in sections[area]:
                rows.append([button("▶️ " + name, "researchmode" if area == "research" else "legacy", name)])
        elif screen == "help":
            text = t["help_text"]
            nav(t["agent"], "agent")
            nav(t["settings"], "settings")
            nav(t["advanced"], "advanced")
        else:
            raise ValueError("unknown screen")
        if screen != "home":
            nav(t["back"], "credentials" if screen in {"keys", "hostkeys"} else "home")
        return text, InlineKeyboardMarkup(rows)

    async def on_text(self, update: Any, context: Any) -> bool:
        if not self.allowed(update) or update.effective_user is None:
            return False
        message = update.effective_message
        if message is None or not isinstance(getattr(message, "text", None), str):
            return False
        owner = str(update.effective_user.id)
        text = message.text.strip()
        key = BINDINGS.get(text)
        if key:
            context.user_data.pop("ui_wait", None)
            context.user_data.pop("ui_wizard", None)
            if key in ("direct", "daily"):
                context.user_data["ui_wait"] = key
                await message.reply_text(WORDS[await self._lang(owner)]["input"])
            else:
                await self.show(update, context, key)
            return True
        wizard = context.user_data.get("ui_wizard")
        if wizard and wizard.get("mode") == "host":
            if getattr(getattr(update, "effective_chat", None), "type", None) != "private" or not self.admin_allowed(update):
                context.user_data.pop("ui_wizard", None)
                return True
            if wizard["step"] == "label":
                if not LABEL_RE.fullmatch(text):
                    await message.reply_text(WORDS[await self._lang(owner)]["error"])
                    return True
                wizard["name"] = text
                wizard["step"] = "value"
                await message.reply_text(WORDS[await self._lang(owner)]["host_value"])
                return True
            if wizard["step"] == "value":
                try:
                    await self.host_credentials.save(wizard["scope"], wizard["name"], text,
                                                     replace=wizard.get("replace", False))
                    context.user_data.pop("ui_wizard", None)
                    await message.reply_text(WORDS[await self._lang(owner)]["saved"]
                                             + "\n" + WORDS[await self._lang(owner)]["restart_required"])
                except Exception:
                    log.warning("Host credential operation failed")
                    await message.reply_text(WORDS[await self._lang(owner)]["error"])
                finally:
                    try:
                        await message.delete()
                    except Exception:
                        log.warning("Could not remove sensitive chat input")
                return True
        if wizard and wizard.get("mode") == "key_add":
            if not self.admin_allowed(update) or getattr(getattr(update, "effective_chat", None), "type", None) != "private":
                context.user_data.pop("ui_wizard", None)
                return True
            stage = wizard["step"]
            if stage in {"provider", "label"}:
                from bridge.services.provider_keys import validate_name
                try:
                    validate_name(text)
                except ValueError:
                    await message.reply_text(WORDS[await self._lang(owner)]["error"])
                    return True
                wizard[stage] = text
                wizard["step"] = "label" if stage == "provider" else "secret"
                lang = await self._lang(owner)
                await message.reply_text(WORDS[lang]["key_label" if stage == "provider" else "key_secret"])
                return True
            if stage == "secret":
                try:
                    await self.provider_keys.add(wizard["provider"], wizard["label"], text)
                    await self.provider_keys.activate(wizard["provider"], wizard["label"])
                    context.user_data.pop("ui_wizard", None)
                    await message.reply_text(WORDS[await self._lang(owner)]["saved"])
                except Exception:
                    log.warning("Provider key enrollment failed")
                    await message.reply_text(WORDS[await self._lang(owner)]["error"])
                finally:
                    try:
                        await message.delete()
                    except Exception:
                        log.warning("Could not delete secret chat message")
                return True
        if wizard:
            step = wizard["step"]
            if step == "name":
                if not 1 <= len(text) <= 60:
                    await message.reply_text("Name must be 1-60 characters.")
                    return True
                wizard["name"] = text
                wizard["step"] = "timing"
                lang = await self._lang(owner)
                await message.reply_text(WORDS[lang]["schedule_time" if wizard["mode"] == "schedule_once" else "schedule_interval"])
                return True
            if step == "timing":
                if not text or len(text) > 80:
                    await message.reply_text("Invalid time / interval.")
                    return True
                wizard["timing"] = text
                wizard["step"] = "prompt"
                await message.reply_text(WORDS[await self._lang(owner)]["schedule_prompt"])
                return True
            if step == "prompt":
                try:
                    zone = (await self.preferences.get(owner)).timezone
                    if wizard["mode"] == "schedule_once":
                        when = local_to_utc(wizard["timing"], zone)
                        job = await self.schedule_service.create_once(
                            owner, update.effective_chat.id, wizard["name"], text,
                            when, timezone_name=zone)
                    else:
                        job = await self.schedule_service.create_recurring(
                            owner, update.effective_chat.id, wizard["name"], text,
                            wizard["timing"], timezone_name=zone)
                    context.user_data.pop("ui_wizard", None)
                    self.wake()
                    await message.reply_text(WORDS[await self._lang(owner)]["saved"] + " #" + str(job.id))
                except Exception:
                    log.exception("Failed to create scheduled task from UI")
                    await message.reply_text(WORDS[await self._lang(owner)]["error"])
                return True
        waiting = context.user_data.pop("ui_wait", None)
        if waiting is None:
            return False
        lang = await self._lang(owner)
        t = WORDS[lang]
        if not text:
            await message.reply_text(t["input"])
            return True
        if waiting == "age_recipient":
            if not self.admin_allowed(update) or getattr(
                getattr(update, "effective_chat", None), "type", None) != "private":
                return True
            from bridge.services.host_credentials import secure_write
            if not text.startswith("age1") or len(text) > 160:
                await message.reply_text(t["error"])
                return True
            secure_write(self.project / "runtime/backup-age-recipient",
                         (text + "\n").encode("ascii"))
            await message.reply_text(t["saved"])
            return True
        if isinstance(waiting, str) and waiting.startswith("mirror:"):
            if not self.admin_allowed(update) or getattr(
                getattr(update, "effective_chat", None), "type", None) != "private":
                return True
            status = await message.reply_text(t["mirror_input"])
            try:
                link = await self.mirrors.mirror_url(waiting.partition(":")[2], text)
                await status.edit_text(t["success"] + "\n" + link)
            except Exception:
                log.warning("Anonymous URL mirror failed")
                await status.edit_text(t["error"])
            return True
        if waiting in {"download", "media"}:
            status = await message.reply_text(t["input"])
            try:
                record = await self.downloads.fetch(owner, text, media=waiting == "media")
                await status.edit_text(t["success"])
                await deliver_download(context.bot, update.effective_chat.id, record)
            except Exception:
                log.exception("button download failed")
                await status.edit_text(t["error"])
            return True
        if isinstance(waiting, str) and waiting.startswith("legacy:"):
            if not self.admin_allowed(update):
                return True
            try:
                await self.legacy_dispatch(waiting.partition(":")[2], update, context, text)
            except Exception:
                log.exception("Advanced button command failed")
                await message.reply_text(t["error"])
            return True
        if isinstance(waiting, str) and waiting.startswith("researchmode:"):
            if not self.admin_allowed(update):
                return True
            mode = self.research_modes.get(waiting.partition(":")[2])
            if mode is None:
                await message.reply_text(t["error"])
                return True
            status = await message.reply_text(t["queued"])
            try:
                queued = await self.tasks.enqueue_prompt(
                    owner, update.effective_chat.id, text,
                    status_message_id=status.message_id,
                    execution_mode=mode,
                    idempotency_scope="telegram_update", idempotency_key=str(update.update_id))
                await status.edit_text(t["queued"] + str(queued.task.id))
                self.wake()
            except Exception:
                log.exception("Advanced research queueing failed")
                await status.edit_text(t["error"])
            return True
        if waiting == "tz_custom":
            try:
                await self.preferences.update(owner, timezone=validate_zone(text))
                await message.reply_text(t["saved"])
            except ValueError:
                context.user_data["ui_wait"] = "tz_custom"
                await message.reply_text(t["tz_prompt"])
            return True
        if waiting not in {"direct", "daily", "research"}:
            return False
        status = await message.reply_text(t["queued"])
        try:
            if waiting == "research":
                from prompt_enhancer import ResearchMode
                opts = {"execution_mode": ResearchMode.DEEP_RESEARCH}
            else:
                opts = {}
            queued = await self.tasks.enqueue_prompt(
                owner, update.effective_chat.id, text,
                status_message_id=status.message_id,
                direct_dispatch=True, daily_continuation=waiting == "daily",
                idempotency_scope="telegram_update",
                idempotency_key=str(update.update_id), **opts)
            await status.edit_text(t["queued"] + str(queued.task.id))
            self.wake()
        except Exception:
            log.exception("button task submission failed")
            await status.edit_text(t["error"])
        return True

    async def handle(self, update: Any, context: Any) -> None:
        query = update.callback_query
        if query is None or not isinstance(query.data, str) or not query.data.startswith("ux:"):
            return
        if not self.allowed(update) or update.effective_user is None:
            await query.answer("Unauthorized", show_alert=True)
            return
        owner = str(update.effective_user.id)
        lang = await self._lang(owner)
        t = WORDS[lang]
        try:
            parts = query.data.split(":")
            action = parts[1]
            screen = "home"
            private_operations = {
                "hostadd", "hostscope", "hostreplace", "hostactivate", "keysel", "keyauto",
                "backupcreate", "backupsend", "backupsecure", "mirrorurl",
                "mirrorfile", "toggleos", "togglebackuppurge", "autopilot", "toggleupdate", "checkbridge",
            }
            if (action in private_operations
                    or (action == "go" and len(parts) > 2
                        and parts[2] in {"credentials", "keys", "hostkeys", "backups", "mirrors"})
                    or (action == "input" and len(parts) > 2 and parts[2] == "key_add")
                    or (action in {"confirm", "execute"} and len(parts) > 2
                        and parts[2] in {"key", "hostkey", "backup"})):
                if not self.admin_allowed(update) or getattr(
                    getattr(update, "effective_chat", None), "type", None) != "private":
                    await query.answer(t["admin_private"], show_alert=True)
                    return
            if (action in {"workers", "keysel", "keyauto", "toggleupdate", "zram", "swap", "legacy", "researchmode"}
                    or (action == "input" and len(parts) > 2 and parts[2] in {"key_add", "age_recipient"})
                    or (action == "confirm" and len(parts) > 2 and parts[2] in {"key"})
                    or (action == "execute" and len(parts) > 2 and parts[2] in {"key"})):
                if not self.admin_allowed(update):
                    await query.answer("Admin only", show_alert=True)
                    return
            if action == "go":
                screen = parts[2]
                restricted = {"credentials", "keys", "hostkeys", "backups", "mirrors", "server",
                              "resources", "updates", "memory", "workers", "advanced"}
                if screen in restricted or screen.startswith("advanced."):
                    if (not self.admin_allowed(update)
                            or getattr(getattr(update, "effective_chat", None), "type", None) != "private"):
                        await query.answer(t["admin_private"], show_alert=True)
                        return
            elif action == "input":
                mode = parts[2]
                if mode not in {"direct", "daily", "download", "media", "research", "schedule_once", "schedule_recurring", "key_add", "tz_custom", "age_recipient"}:
                    raise ValueError("invalid input mode")
                if mode == "key_add":
                    context.user_data.pop("ui_wait", None)
                    context.user_data["ui_wizard"] = {"mode": mode, "step": "provider"}
                    prompt = t["key_provider"]
                elif mode in {"schedule_once", "schedule_recurring"}:
                    context.user_data.pop("ui_wait", None)
                    context.user_data["ui_wizard"] = {"mode": mode, "step": "name"}
                    prompt = t["schedule_name"]
                else:
                    context.user_data.pop("ui_wizard", None)
                    context.user_data["ui_wait"] = mode
                    prompt = t["backup_recipient"] if mode == "age_recipient" else t["tz_prompt"] if mode == "tz_custom" else (t["media_input"] if mode == "media" else t["download_input"] if mode == "download" else (t["research_input"] if mode == "research" else t["input"]))
                await query.answer()
                await update.effective_message.reply_text(prompt)
                return
            elif action == "hostadd":
                keyboard = InlineKeyboardMarkup([
                    [button(scope, "hostscope", scope)]
                    for scope in ("github", "telegram", "opencode", "opencode-server")
                ])
                await update.get_bot().edit_message_text(
                    chat_id=query.message.chat_id, message_id=query.message.message_id,
                    text=t["host_scope"], reply_markup=keyboard)
                await query.answer()
                return
            elif action == "hostscope":
                from bridge.services.host_credentials import ALLOWED
                scope = parts[2]
                if scope not in ALLOWED:
                    raise ValueError("Unknown host credential type")
                context.user_data["ui_wizard"] = {
                    "mode": "host", "step": "label", "scope": scope}
                await query.answer()
                await update.effective_message.reply_text(t["host_label"])
                return
            elif action == "hostreplace":
                entries = self.host_credentials.catalog()
                index = int(parts[2])
                if not 0 <= index < min(len(entries), 24):
                    raise ValueError("Outdated host credential index")
                item = entries[index]
                if not item.get("managed", True):
                    raise ValueError("External credential is not managed")
                context.user_data["ui_wizard"] = {
                    "mode": "host", "step": "value",
                    "scope": item["scope"], "name": item["name"], "replace": True}
                await query.answer()
                await update.effective_message.reply_text(t["host_value"])
                return
            elif action == "hostactivate":
                entries = self.host_credentials.catalog()
                index = int(parts[2])
                if not 0 <= index < min(len(entries), 24):
                    raise ValueError("Outdated host credential index")
                item = entries[index]
                await self.host_credentials.activate(item["scope"], item["name"])
                await update.effective_message.reply_text(t["restart_required"])
                screen = "hostkeys"
            elif action == "keysel":
                entries = self.provider_keys.catalog()
                idx = int(parts[2])
                if not 0 <= idx < min(len(entries), 30):
                    raise ValueError("stale key")
                entry = entries[idx]
                await self.provider_keys.activate(entry["provider"], entry["name"])
                screen = "keys"
            elif action == "keyauto":
                entries = self.provider_keys.catalog()
                providers = sorted({item["provider"] for item in entries})
                idx = int(parts[2])
                if not 0 <= idx < len(providers):
                    raise ValueError("stale provider")
                await self.provider_keys.toggle_auto(providers[idx])
                screen = "keys"
            elif action == "backupcreate":
                await query.answer()
                created = await self.backups.create()
                await update.effective_message.reply_text(
                    t["success"] + "\n" + safe(created["name"]))
                screen = "backups"
            elif action == "backupsend":
                entries = self.backups.list()
                index = int(parts[2])
                if not 0 <= index < min(len(entries), 12):
                    raise ValueError("Missing backup")
                item = self.backups.get(entries[index]["name"])
                record = SimpleNamespace(
                    path=str(item), size_bytes=item.stat().st_size,
                    filename=item.name, direct_send_limit=49 * 1024 * 1024)
                await deliver_download(context.bot, update.effective_chat.id, record)
                screen = "backups"
            elif action == "backupsecure":
                await query.answer()
                created = await self.backups.create_encrypted()
                await update.effective_message.reply_text(
                    t["success"] + "\n" + safe(created["name"]))
                screen = "backups"
            elif action == "mirrorurl":
                from bridge.services.mirrors import PROVIDERS
                if parts[2] not in PROVIDERS or not PROVIDERS[parts[2]]["url"]:
                    raise ValueError("Unsupported URL mirror")
                context.user_data["ui_wait"] = "mirror:" + parts[2]
                await query.answer()
                await update.effective_message.reply_text(t["mirror_input"])
                return
            elif action == "mirrorfile":
                from bridge.services.mirrors import PROVIDERS
                provider = parts[2]
                index = int(parts[3])
                records = self.downloads.list(owner)
                if provider not in PROVIDERS or not 0 <= index < min(len(records), 8):
                    raise ValueError("Invalid mirror selection")
                link = await self.mirrors.mirror_file(provider, __import__("pathlib").Path(records[index].path))
                await update.effective_message.reply_text(link)
                screen = "mirrors"
            elif action == "checkbridge":
                await query.answer()
                notice = await update.effective_message.reply_text(t["pending_update"])

                async def finish_bridge_check() -> None:
                    try:
                        result = await update_now("bridge-check")
                        await notice.edit_text(result[-3500:])
                    except Exception:
                        log.exception("Bridge update check failed")
                        await notice.edit_text(t["error"])

                task = asyncio.create_task(finish_bridge_check())
                self._running_updates.add(task)
                task.add_done_callback(self._running_updates.discard)
                return
            elif action == "autopilot":
                if len(parts) != 3:
                    raise ValueError("invalid policy callback")
                self.runtime_controls.set_autopilot_mode(parts[2])
                screen = "updates"
            elif action == "toggleupdate":
                # Legacy callbacks retain their meaning without bypassing policy.
                self.runtime_controls.set_auto_update(not self.runtime_controls.auto_update())
                screen = "updates"
            elif action == "toggleos":
                self.runtime_controls.set_system_auto_update(
                    not self.runtime_controls.system_auto_update())
                screen = "updates"
            elif action == "togglebackuppurge":
                self.runtime_controls.set_purge_backups_after_update(
                    not self.runtime_controls.purge_backups_after_update())
                screen = "updates"
            elif action == "zram":
                self.runtime_controls.set_memory(zram_percent=parts[2] if parts[2] == "auto" else int(parts[2]))
                screen = "memory"
            elif action == "swap":
                value = parts[2] if parts[2] == "auto" else int(parts[2])
                self.runtime_controls.set_memory(swap_gib=value)
                screen = "memory"
            elif action == "legacy":
                name = parts[2]
                if name not in ["new","abort","share","unshare","status","health","agents","model","tasks","failed","progress","trace","cancel","retry","discard","schedules","schedule","repeat","schedshow","schedhistory","schedrename","schededit","schedappend","schedtime","schedinterval","schedtimezone","schedrun","schedpause","schedresume","scheddelete","draft","config","limits","maintenance","download","deletefile"] or self.legacy_dispatch is None:
                    raise ValueError("Unknown legacy command")
                if name in ["new","abort","share","unshare","status","health","agents","model","tasks","failed","progress","trace","cancel","discard","schedules","config","limits","maintenance"]:
                    await self.legacy_dispatch(name, update, context, "")
                    screen = "advanced"
                else:
                    context.user_data["ui_wait"] = "legacy:" + name
                    await update.effective_message.reply_text(t["advanced_args"])
                    await query.answer()
                    return
            elif action == "researchmode":
                name = parts[2]
                if name not in self.research_modes:
                    raise ValueError("Unknown research mode")
                context.user_data["ui_wait"] = "researchmode:" + name
                await update.effective_message.reply_text(t["input"])
                await query.answer()
                return
            elif action == "lang":
                if parts[2] not in {"ar", "en"}:
                    raise ValueError("unsupported language")
                await self.preferences.update(owner, language=parts[2])
                screen = "home"
                # Updating the reply keyboard is independent of editing the inline menu.
                await update.effective_message.reply_text(
                    WORDS[parts[2]]["saved"], reply_markup=self.keyboard(parts[2]))
            elif action == "tz":
                index = int(parts[2])
                if not 0 <= index < len(COUNTRIES):
                    raise ValueError("invalid country index")
                await self.preferences.update(owner, timezone=COUNTRIES[index][1])
                screen = "timezone"
            elif action == "priority":
                changed = await self.tasks.change_priority(owner, int(parts[2]), int(parts[3]))
                if changed is None:
                    raise ValueError("cannot reprioritize running task")
                self.wake()
                screen = "tasks"
            elif action == "workers":
                limit = int(parts[2])
                self.capacity.set(limit)
                self.wake()
                screen = "workers"
            elif action == "agent":
                agents = await self.agents.agents()
                name = None
                if parts[2] != "auto":
                    idx = int(parts[2])
                    if idx < 0 or idx >= min(len(agents), 35):
                        raise ValueError("stale agent")
                    item = agents[idx]
                    name = item.get("name") if isinstance(item, dict) else None
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError("invalid agent")
                await self.preferences.update(owner, agent_preference=name)
                screen = "agent"
            elif action == "model":
                if parts[2] == "auto":
                    await self.models.auto(owner)
                    screen = "agent"
                else:
                    index = int(parts[2])
                    await self.models.select_model(owner, index)
                    screen = f"reason.{index}"
            elif action == "reason":
                await self.models.select_variant(owner, int(parts[2]), int(parts[3]))
                screen = "agent"
            elif action == "confirm":
                if parts[2] not in {"cancel", "schedule", "file", "new", "key", "task", "hostkey", "backup", "updateagent", "updatesystem", "updatebridge"}:
                    raise ValueError("unsupported confirmation")
                context.user_data["ui_confirm"] = (parts[2], parts[3])
                text = t["confirm_text"]
                keyboard = InlineKeyboardMarkup([[
                    button(t["yes"], "execute", parts[2], parts[3]),
                    button(t["no"], "go", "home")]])
                await update.get_bot().edit_message_text(
                    chat_id=query.message.chat_id, message_id=query.message.message_id,
                    text=text, reply_markup=keyboard, parse_mode="HTML")
                await query.answer()
                return
            elif action == "execute":
                kind, value = parts[2], parts[3]
                if context.user_data.pop("ui_confirm", None) != (kind, value):
                    raise ValueError("confirmation required")
                if kind in {"hostkey", "backup", "updateagent", "updatesystem", "updatebridge"}:
                    if not self.admin_allowed(update) or getattr(
                        getattr(update, "effective_chat", None), "type", None) != "private":
                        raise ValueError("Private administrator chat required")
                if kind == "hostkey":
                    entries = self.host_credentials.catalog()
                    idx = int(value)
                    if not 0 <= idx < min(len(entries), 24):
                        raise ValueError("Stale credential index")
                    entry = entries[idx]
                    await self.host_credentials.delete(entry["scope"], entry["name"])
                    screen = "hostkeys"
                elif kind == "backup":
                    entries = self.backups.list()
                    idx = int(value)
                    if not 0 <= idx < min(len(entries), 12):
                        raise ValueError("Stale backup index")
                    self.backups.delete(entries[idx]["name"])
                    screen = "backups"
                elif kind in {"updateagent", "updatesystem", "updatebridge"}:
                    await query.answer()
                    notice = await update.effective_message.reply_text(t["pending_update"])
                    kind_name = {"updateagent": "agent", "updatesystem": "system", "updatebridge": "bridge"}[kind]
                    async def complete_update() -> None:
                        result = await update_now(kind_name, progress=lambda chunk: notice.edit_text((kind_name + ': ' + chunk)[-3500:]))
                        await notice.edit_text(result[-3500:])
                    task = asyncio.create_task(complete_update())
                    self._running_updates.add(task)
                    task.add_done_callback(self._running_updates.discard)
                    return
                elif kind == "new":
                    await self.agents.fresh_session(owner)
                    screen = "agent"
                elif kind == "key":
                    entries = self.provider_keys.catalog()
                    idx = int(value)
                    if not 0 <= idx < min(len(entries), 30):
                        raise ValueError("stale key")
                    entry = entries[idx]
                    await self.provider_keys.remove(entry["provider"], entry["name"])
                    screen = "keys"
                elif kind == "task":
                    if await self.tasks.remove(owner, int(value)) is None:
                        raise ValueError("running task cannot be deleted")
                    self.wake()
                    screen = "tasks"
                elif kind == "cancel":
                    if await self.tasks.cancel(int(value), owner) is None:
                        raise ValueError("task not active")
                    screen = "tasks"
                elif kind == "schedule":
                    job = await self.schedule_store.get_scheduled_job_by_id(int(value), owner)
                    if job is None:
                        raise ValueError("stale schedule")
                    await self.schedule_store.delete_scheduled_job(owner, job.name)
                    screen = "schedules"
                elif kind == "file":
                    records = self.downloads.list(owner)
                    idx = int(value)
                    if not 0 <= idx < min(12, len(records)):
                        raise ValueError("stale file")
                    self.downloads.delete(owner, records[idx].id)
                    screen = "downloads"
                else:
                    raise ValueError("unsupported action")
            elif action == "retry":
                if await self.tasks.retry_failed(owner, int(parts[2])) is None:
                    raise ValueError("stale task")
                self.wake()
                screen = "tasks"
            elif action == "schedule":
                job = await self.schedule_store.get_scheduled_job_by_id(int(parts[2]), owner)
                if job is None:
                    raise ValueError("stale schedule")
                await self.schedule_store.set_scheduled_job_enabled(owner, job.name, not job.enabled)
                screen = "schedules"
            elif action == "file":
                records = self.downloads.list(owner)
                idx = int(parts[2])
                if not 0 <= idx < min(12, len(records)):
                    raise ValueError("stale file")
                record = records[idx]
                await deliver_download(context.bot, update.effective_chat.id, record)
                screen = "downloads"
            else:
                raise ValueError("unknown callback action")
            text, markup = await self.render(owner, screen)
            await update.get_bot().edit_message_text(
                chat_id=query.message.chat_id, message_id=query.message.message_id,
                text=text, reply_markup=markup, parse_mode="HTML",
                disable_web_page_preview=True)
            await query.answer()
        except (ValueError, IndexError, KeyError):
            await query.answer(t["not_found"], show_alert=True)
        except Exception:
            log.exception("Control UI operation failed")
            await query.answer(t["error"], show_alert=True)
