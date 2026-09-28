"""Thin Telegram adapter for durable drafts."""

from __future__ import annotations


class DraftCommands:
    def __init__(self, service, reply):
        self.service = service
        self.reply = reply

    @staticmethod
    def _owner(update):
        return str(update.effective_user.id)

    async def handle(self, update, context):
        args = list(context.args)
        if not args:
            await self.reply(update.message, "الصيغة: /draft new|show|clear|save|run|schedule ...")
            return
        action = args.pop(0).lower()
        owner = self._owner(update)
        try:
            if action == "new":
                draft = await self.service.new(owner, " ".join(args))
                context.user_data["active_draft"] = draft.name
                text = f"بدأ Draft «{draft.name}»."
            elif action == "show":
                draft = await self.service.show(owner, " ".join(args))
                text = f"Draft «{draft.name}» v{draft.version}\n{draft.prompt_text or '(فارغ)'}\nالمرفقات: {len(draft.attachments)}"
            elif action == "clear":
                draft = await self.service.clear(owner, " ".join(args))
                text = f"تم مسح «{draft.name}» مع حفظ الإصدار السابق."
            elif action == "save":
                draft = await self.service.save(owner, " ".join(args))
                context.user_data.pop("active_draft", None)
                text = f"تم حفظ «{draft.name}» v{draft.version}."
            elif action == "run":
                name = " ".join(args)
                result = await self.service.run(owner, update.effective_chat.id, name, idempotency_key=f"{update.update_id}:{name}")
                text = f"تم إرسال Draft «{name}» للتنفيذ."
            elif action == "schedule":
                raw = " ".join(args)
                name, schedule_name, due = [part.strip() for part in raw.split("|", 2)]
                await self.service.schedule(owner, update.effective_chat.id, name, schedule_name, due)
                text = f"تمت جدولة Draft «{name}» باسم «{schedule_name}»."
            else:
                text = "أمر Draft غير معروف."
        except (KeyError, ValueError) as exc:
            text = f"تعذر تنفيذ أمر Draft: {exc}"
        await self.reply(update.message, text)

    async def capture_text(self, update, context) -> bool:
        name = context.user_data.get("active_draft")
        if not name or update.message is None or not update.message.text:
            return False
        await self.service.append(self._owner(update), name, text=update.message.text)
        await self.reply(update.message, f"أضيف النص إلى «{name}».")
        return True
