"""Persistent scheduling application service.

This module owns schedule business rules and deliberately has no Telegram
imports. The current TaskQueueStore is injected as a compatibility repository
until the T04 database-layer migration.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from bridge.domain.policies import RequestGuard
from bridge.domain.schedules import parse_interval_seconds

UTC = timezone.utc


class ScheduleRepository(Protocol):
    async def create_scheduled_job(
        self,
        owner_id: str,
        chat_id: int,
        name: str,
        prompt: str,
        next_run_at: datetime,
        repeat_seconds: int | None = None,
        timezone_name: str = "UTC",
    ) -> Any: ...

    async def get_scheduled_job(self, owner_id: str, name: str) -> Any | None: ...
    async def list_scheduled_jobs(self, owner_id: str, limit: int = 100) -> list[Any]: ...
    async def rename_scheduled_job(self, owner_id: str, name: str, new_name: str) -> Any | None: ...
    async def set_scheduled_job_prompt(self, owner_id: str, name: str, prompt: str) -> Any | None: ...
    async def append_scheduled_job_prompt(self, owner_id: str, name: str, text: str) -> Any | None: ...
    async def update_scheduled_job_timing(
        self,
        owner_id: str,
        name: str,
        next_run_at: datetime,
        repeat_seconds: int | None,
        timezone_name: str = "UTC",
    ) -> Any | None: ...
    async def set_scheduled_job_enabled(self, owner_id: str, name: str, enabled: bool) -> Any | None: ...
    async def delete_scheduled_job(self, owner_id: str, name: str) -> bool: ...
    async def enqueue_scheduled_job_now(
        self,
        owner_id: str,
        name: str,
        status_message_id: int | None = None,
    ) -> Any | None: ...


class ScheduleNotFound(LookupError):
    pass


class ScheduleService:
    def __init__(
        self,
        repository: ScheduleRepository,
        guard: RequestGuard,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.guard = guard
        self._clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    async def list(self, owner_id: str, limit: int = 100) -> list[Any]:
        return await self.repository.list_scheduled_jobs(owner_id, limit=limit)

    async def get(self, owner_id: str, name: str) -> Any:
        job = await self.repository.get_scheduled_job(owner_id, name)
        if job is None:
            raise ScheduleNotFound(name)
        return job

    async def create_once(
        self,
        owner_id: str,
        chat_id: int,
        name: str,
        prompt: str,
        due_at: datetime,
        timezone_name: str = "UTC",
    ) -> Any:
        self.guard.ensure_allowed(prompt)
        return await self.repository.create_scheduled_job(
            owner_id,
            chat_id,
            name,
            prompt,
            due_at,
            repeat_seconds=None,
            timezone_name=timezone_name,
        )

    async def create_recurring(
        self,
        owner_id: str,
        chat_id: int,
        name: str,
        prompt: str,
        interval_text: str,
        timezone_name: str = "UTC",
    ) -> Any:
        self.guard.ensure_allowed(prompt)
        repeat_seconds = parse_interval_seconds(interval_text)
        due_at = self.now() + timedelta(seconds=repeat_seconds)
        return await self.repository.create_scheduled_job(
            owner_id,
            chat_id,
            name,
            prompt,
            due_at,
            repeat_seconds=repeat_seconds,
            timezone_name=timezone_name,
        )

    async def rename(self, owner_id: str, name: str, new_name: str) -> Any:
        job = await self.repository.rename_scheduled_job(owner_id, name, new_name)
        if job is None:
            raise ScheduleNotFound(name)
        return job

    async def replace_prompt(self, owner_id: str, name: str, prompt: str) -> Any:
        self.guard.ensure_allowed(prompt)
        job = await self.repository.set_scheduled_job_prompt(owner_id, name, prompt)
        if job is None:
            raise ScheduleNotFound(name)
        return job

    async def append_prompt(self, owner_id: str, name: str, addition: str) -> Any:
        current = await self.get(owner_id, name)
        combined = current.prompt.rstrip() + "\n" + addition.rstrip()
        self.guard.ensure_allowed(combined)
        job = await self.repository.append_scheduled_job_prompt(owner_id, name, addition)
        if job is None:
            raise ScheduleNotFound(name)
        return job

    async def change_next_run(self, owner_id: str, name: str, due_at: datetime) -> Any:
        current = await self.get(owner_id, name)
        job = await self.repository.update_scheduled_job_timing(
            owner_id,
            name,
            due_at,
            current.repeat_seconds,
            timezone_name=current.timezone_name,
        )
        if job is None:
            raise ScheduleNotFound(name)
        return job

    async def change_interval(self, owner_id: str, name: str, interval_text: str) -> tuple[Any, str]:
        current = await self.get(owner_id, name)
        normalized = interval_text.strip().lower()
        if normalized in {"once", "one", "مرة", "مرة واحدة"}:
            repeat_seconds = None
            due_at = current.next_run_at
            if due_at is None or due_at <= self.now():
                due_at = self.now() + timedelta(minutes=1)
            label = "مرة واحدة"
        else:
            repeat_seconds = parse_interval_seconds(interval_text)
            due_at = self.now() + timedelta(seconds=repeat_seconds)
            label = interval_text
        job = await self.repository.update_scheduled_job_timing(
            owner_id,
            name,
            due_at,
            repeat_seconds,
            timezone_name=current.timezone_name,
        )
        if job is None:
            raise ScheduleNotFound(name)
        return job, label

    async def pause(self, owner_id: str, name: str) -> Any:
        job = await self.repository.set_scheduled_job_enabled(owner_id, name, False)
        if job is None:
            raise ScheduleNotFound(name)
        return job

    async def resume(self, owner_id: str, name: str) -> Any:
        job = await self.repository.set_scheduled_job_enabled(owner_id, name, True)
        if job is None:
            raise ScheduleNotFound(name)
        return job

    async def delete(self, owner_id: str, name: str) -> None:
        if not await self.repository.delete_scheduled_job(owner_id, name):
            raise ScheduleNotFound(name)

    async def run_now(
        self,
        owner_id: str,
        name: str,
        status_message_id: int | None = None,
    ) -> tuple[Any, Any]:
        job = await self.get(owner_id, name)
        task = await self.repository.enqueue_scheduled_job_now(
            owner_id,
            job.name,
            status_message_id=status_message_id,
        )
        if task is None:
            raise ScheduleNotFound(name)
        return job, task
