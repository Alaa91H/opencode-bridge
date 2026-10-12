"""Bilingual, owner-authorized Telegram button control surface.

Inline callback data is short, revalidated on every press and never carries
user-supplied paths, model identifiers or agent names. Input actions use a
one-message state in Telegram user_data; raw text remains a normal prompt
outside that state.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

log = logging.getLogger(__name__)

WORDS = {
    "ar": {
        "home": "🏠 الرئيسية", "agent": "🤖 إدارة الوكيل", "tasks": "📋 المهام",
        "schedules": "📅 الجدولة", "downloads": "📁 الملفات", "settings": "⚙️ الإعدادات", "keys": "🔑 مفاتيح API", "key_add": "➕ إضافة مفتاح", "key_auto": "🔄 تدوير تلقائي",
        "language": "🌐 اللغة", "models": "🧠 النموذج والتفكير",
        "agents": "🔄 تبديل الوكيل", "workers": "⚡ المهام المتزامنة",
        "direct": "✉️ أمر مباشر", "daily": "🔁 مهمة يومية",
        "research": "🔍 بحث معمّق", "help": "❔ المساعدة",
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
        "schedule_time": "أرسل الوقت بصيغة 2026-10-15 12:00 UTC.",
        "schedule_interval": "أرسل الفاصل الزمني مثل 1d أو 12h أو 30m.",
        "schedule_prompt": "أرسل تعليمات المهمة المجدولة.",
        "download_input": "أرسل رابط HTTPS أو HTTP لتنزيل الملف.",
        "key_provider": "أرسل معرّف المزوّد مثل openai أو anthropic.",
        "key_label": "أرسل اسمًا للمفتاح (حروف إنجليزية وأرقام فقط).",
        "key_secret": "أرسل قيمة API Key الآن. ستُحذف الرسالة مباشرة.",
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
        "success": "تمت العملية.", "number_one": "1 — موصى به", "number_two": "2 — عند الحاجة",
    },
    "en": {
        "home": "🏠 Home", "agent": "🤖 Agent manager", "tasks": "📋 Tasks",
        "schedules": "📅 Schedules", "downloads": "📁 Files", "settings": "⚙️ Settings", "keys": "🔑 API keys", "key_add": "➕ Add key", "key_auto": "🔄 Auto rotation",
        "language": "🌐 Language", "models": "🧠 Model & reasoning",
        "agents": "🔄 Switch agent", "workers": "⚡ Parallel tasks",
        "direct": "✉️ Direct task", "daily": "🔁 Daily task",
        "research": "🔍 Deep research", "help": "❔ Help",
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
        "schedule_time": "Send UTC date/time like 2026-10-15 12:00 UTC.",
        "schedule_interval": "Send an interval such as 1d, 12h or 30m.",
        "schedule_prompt": "Send the task instructions to schedule.",
        "download_input": "Send an HTTPS or HTTP URL to download.",
        "key_provider": "Send provider ID, e.g. openai or anthropic.",
        "key_label": "Send key label (letters/digits only).",
        "key_secret": "Send the API key value now. The message will be deleted.",
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
        "success": "Action completed.", "number_one": "1 — recommended", "number_two": "2 — if needed",
    },
}
BUTTON_KEYS = (("home", "agent"), ("keys", "direct"), ("daily", "tasks"), ("schedules", "downloads"), ("settings", "language"), ("help",))
BINDINGS = {value: key for lang in WORDS.values() for key in
            ("home", "agent", "keys", "direct", "daily", "tasks", "schedules", "downloads", "settings", "language", "help")
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
                 capacity: Any, wake: Any, allowed: Any, provider_keys: Any | None = None):
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
            for key in ("agent", "keys", "direct", "daily", "research", "tasks", "schedules", "downloads", "settings", "help"):
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
        elif screen == "tasks":
            text = t["tasks_text"]
            active = await self.tasks.active(owner)
            failed = await self.tasks.failed(owner)
            if not active and not failed:
                text += "\n" + t["empty"]
            for task in active[:8]:
                text += "\n• " + safe(task.status) + " — " + safe(task.prompt, 70)
                rows.append([button(t["stop"] + " #" + str(task.id), "confirm", "cancel", task.id)])
            for task in failed[:8]:
                text += "\n• " + safe(task.status) + " — " + safe(task.prompt, 70)
                rows.append([button(t["retry"] + " #" + str(task.id), "retry", task.id)])
            rows.append([button(t["direct"], "input", "direct"), button(t["daily"], "input", "daily")])
            nav(t["refresh"], "tasks")
        elif screen == "schedules":
            text = t["schedules_text"]
            jobs = await self.schedule_store.list_scheduled_jobs(owner)
            if not jobs:
                text += "\n" + t["empty"]
            for job in jobs[:15]:
                text += "\n• " + safe(job.name, 60)
                rows.append([button((t["on"] if job.enabled else t["off"]) + " " + job.name[:25], "schedule", job.id),
                             button(t["delete"], "confirm", "schedule", job.id)])
            rows.append([button(t["create_schedule"], "input", "schedule_once"),
                         button(t["create_repeat"], "input", "schedule_recurring")])
            nav(t["refresh"], "schedules")
        elif screen == "downloads":
            text = t["download_text"]
            records = self.downloads.list(owner)
            for i, item in enumerate(records[:12]):
                text += "\n• " + safe(item.filename, 80)
                rows.append([button("📤 " + item.filename[:28], "file", i),
                             button(t["delete"], "confirm", "file", i)])
            rows.append([button("🔗 " + t["downloads"], "input", "download")])
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
        elif screen == "settings":
            text = t["settings_text"]
            nav(t["language"], "language")
            nav(t["workers"], "workers")
            nav(t["agent"], "agent")
            nav(t["keys"], "keys")
        elif screen == "language":
            text = t["language_text"]
            rows.extend([[button("🇸🇦 العربية", "lang", "ar")],
                         [button("🇬🇧 English", "lang", "en")]])
        elif screen == "workers":
            limit = self.capacity.get()
            text = t["worker_text"].format(limit=limit)
            for n, key in ((1, "number_one"), (2, "number_two")):
                rows.append([button(("✅ " if limit == n else "") + t[key], "workers", n)])
        elif screen == "help":
            text = t["help_text"]
            nav(t["agent"], "agent")
            nav(t["settings"], "settings")
        else:
            raise ValueError("unknown screen")
        if screen != "home":
            nav(t["back"], "home")
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
        if wizard and wizard.get("mode") == "key_add":
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
                    log.exception("Provider key enrollment failed (secret redacted)")
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
                    if wizard["mode"] == "schedule_once":
                        from datetime import UTC, datetime
                        when = datetime.fromisoformat(wizard["timing"].replace(" UTC", "+00:00").replace("Z", "+00:00"))
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=UTC)
                        job = await self.schedule_service.create_once(owner, update.effective_chat.id, wizard["name"], text, when)
                    else:
                        job = await self.schedule_service.create_recurring(owner, update.effective_chat.id, wizard["name"], text, wizard["timing"])
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
        if waiting == "download":
            status = await message.reply_text(t["input"])
            try:
                record = await self.downloads.fetch(owner, text)
                await status.edit_text(t["success"])
                if record.sendable_inline:
                    with open(record.path, "rb") as file:
                        await context.bot.send_document(chat_id=update.effective_chat.id,
                                                        document=file, filename=record.filename)
            except Exception:
                log.exception("button download failed")
                await status.edit_text(t["error"])
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
            if action == "go":
                screen = parts[2]
            elif action == "input":
                mode = parts[2]
                if mode not in {"direct", "daily", "download", "research", "schedule_once", "schedule_recurring", "key_add"}:
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
                    prompt = t["download_input"] if mode == "download" else (t["research_input"] if mode == "research" else t["input"])
                await query.answer()
                await update.effective_message.reply_text(prompt)
                return
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
            elif action == "lang":
                if parts[2] not in {"ar", "en"}:
                    raise ValueError("unsupported language")
                await self.preferences.update(owner, language=parts[2])
                screen = "home"
                # Updating the reply keyboard is independent of editing the inline menu.
                await update.effective_message.reply_text(
                    WORDS[parts[2]]["saved"], reply_markup=self.keyboard(parts[2]))
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
                if parts[2] not in {"cancel", "schedule", "file", "new", "key"}:
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
                if kind == "new":
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
                if record.sendable_inline:
                    with open(record.path, "rb") as stream:
                        await context.bot.send_document(chat_id=update.effective_chat.id,
                                                        document=stream, filename=record.filename)
                else:
                    await query.answer(t["error"], show_alert=True)
                    return
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
