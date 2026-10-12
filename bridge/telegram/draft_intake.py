"""Draft-aware Telegram intake preserving thin legacy handlers."""

from __future__ import annotations


class DraftIntakeAdapter:
    def __init__(self, drafts, media, attachment_store, reply, task_commands, control_ui=None):
        self.drafts = drafts
        self.media = media
        self.attachment_store = attachment_store
        self.reply = reply
        self.task_commands = task_commands
        self.control_ui = control_ui

    async def attachment(self, update, context) -> None:
        active = context.user_data.get("active_draft")
        if not active or not update.message or not update.effective_user:
            await self.media.handle_attachment(update, context)
            return
        attachment = await self.attachment_store.download_from_message(
            update.message, context.bot, str(update.effective_user.id)
        )
        await self.drafts.append(
            str(update.effective_user.id),
            active,
            text=(update.message.caption or "").strip(),
            attachments=[attachment.to_record()],
        )
        await self.reply(update.message, f"أضيف المرفق إلى Draft «{active}».")

    async def text(self, update, context) -> None:
        if self.control_ui is not None and await self.control_ui().on_text(update, context):
            return
        active = context.user_data.get("active_draft")
        if active and update.message and update.message.text and update.effective_user:
            await self.drafts.append(str(update.effective_user.id), active, text=update.message.text)
            await self.reply(update.message, f"أضيف النص إلى «{active}».")
            return
        await self.task_commands.text(update, context)
