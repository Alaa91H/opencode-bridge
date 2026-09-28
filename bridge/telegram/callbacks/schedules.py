"""Owner-scoped callbacks for the schedule browser."""

from __future__ import annotations

from bridge.telegram.rendering.schedule_browser import delete_confirmation, schedule_actions, schedule_page
from bridge.telegram.rendering.schedules import render_schedule_detail


class ScheduleCallbacks:
    def __init__(self, service, *, max_message_length: int = 4096):
        self.service = service
        self.max_message_length = max_message_length

    @staticmethod
    def _owner(update) -> str:
        return str(update.effective_user.id)

    async def _job_by_id(self, owner_id: str, job_id: int):
        jobs = await self.service.list(owner_id)
        return next((job for job in jobs if job.id == job_id), None)

    async def handle(self, update, context) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        parts = (query.data or "").split(":")
        if len(parts) < 2 or parts[0] != "sch":
            return
        action = parts[1]
        owner = self._owner(update)
        if action == "noop":
            return
        if action == "page":
            page = int(parts[2])
            jobs = await self.service.list(owner)
            text, markup = schedule_page(jobs, page)
            await query.edit_message_text(text, reply_markup=markup)
            return
        if len(parts) < 4:
            return
        job_id, page = int(parts[2]), int(parts[3])
        job = await self._job_by_id(owner, job_id)
        if job is None:
            await query.edit_message_text("لم تعد هذه الجدولة متاحة.")
            return
        if action == "show":
            await query.edit_message_text(render_schedule_detail(job, self.max_message_length), reply_markup=schedule_actions(job, page))
        elif action == "pause":
            job = await self.service.pause(owner, job.name)
            await query.edit_message_text(render_schedule_detail(job, self.max_message_length), reply_markup=schedule_actions(job, page))
        elif action == "resume":
            job = await self.service.resume(owner, job.name)
            await query.edit_message_text(render_schedule_detail(job, self.max_message_length), reply_markup=schedule_actions(job, page))
        elif action == "history":
            runs = await self.service.history(owner, job.name)
            lines = [f"سجل «{job.name}»:"] + [f"• {r['scheduled_for']} — {r['status']}" for r in runs]
            await query.edit_message_text("\n".join(lines)[:self.max_message_length], reply_markup=schedule_actions(job, page))
        elif action == "duplicate":
            names = {j.name.casefold() for j in await self.service.list(owner)}
            base, n = f"{job.name} copy", 2
            new_name = base
            while new_name.casefold() in names:
                new_name = f"{base} {n}"; n += 1
            clone = await self.service.duplicate(owner, job.name, new_name)
            await query.edit_message_text(f"تم إنشاء نسخة «{clone.name}».", reply_markup=schedule_actions(clone, page))
        elif action == "delete":
            await query.edit_message_text(f"تأكيد حذف «{job.name}»؟", reply_markup=delete_confirmation(job.id, page))
        elif action == "deleteyes":
            await self.service.delete(owner, job.name)
            jobs = await self.service.list(owner)
            text, markup = schedule_page(jobs, page)
            await query.edit_message_text(text, reply_markup=markup)
        elif action == "run":
            await self.service.run_now(owner, job.name)
            await query.edit_message_text(f"تم إرسال «{job.name}» للتنفيذ.", reply_markup=schedule_actions(job, page))
        elif action == "edit":
            context.user_data["schedule_edit_id"] = job.id
            await query.edit_message_text(
                "التعديل النصي الكامل متاح عبر /schedrename و/schededit و/schedtime و/schedinterval. "
                "استخدم هذه الأوامر لتعديل الاسم أو الأمر أو الوقت أو recurrence.",
                reply_markup=schedule_actions(job, page),
            )
