"""Application use-cases for durable prompt drafts."""

from __future__ import annotations


class DraftService:
    def __init__(self, store, task_service=None, schedule_service=None):
        self.store = store
        self.task_service = task_service
        self.schedule_service = schedule_service

    async def new(self, owner_id: str, name: str):
        name = name.strip()
        if not name:
            raise ValueError("draft name is required")
        return await self.store.create(owner_id, name)

    async def show(self, owner_id: str, name: str):
        draft = await self.store.get(owner_id, name)
        if draft is None:
            raise KeyError(name)
        return draft

    async def append(self, owner_id: str, name: str, *, text: str = "", attachments=()):
        if not text and not attachments:
            raise ValueError("empty draft addition")
        return await self.store.append(owner_id, name, text, attachments)

    async def clear(self, owner_id: str, name: str):
        return await self.store.clear(owner_id, name)

    async def save(self, owner_id: str, name: str):
        return await self.store.save(owner_id, name)

    async def run(self, owner_id: str, chat_id: int, name: str, *, idempotency_key: str | None = None):
        draft = await self.show(owner_id, name)
        if self.task_service is None:
            raise RuntimeError("task service unavailable")
        # Draft attachments are durable metadata; T11 will own binary storage.
        return await self.task_service.enqueue_prompt(
            owner_id, chat_id, draft.prompt_text,
            idempotency_scope="draft",
            idempotency_key=idempotency_key,
        )

    async def schedule(self, owner_id: str, chat_id: int, name: str, schedule_name: str, due_text: str):
        draft = await self.show(owner_id, name)
        if self.schedule_service is None:
            raise RuntimeError("schedule service unavailable")
        return await self.schedule_service.create_once(owner_id, chat_id, schedule_name, due_text, draft.prompt_text)
