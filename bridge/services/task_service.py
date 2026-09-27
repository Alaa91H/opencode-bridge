"""Application-level task use cases, separate from worker execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from bridge.domain.policies import RequestGuard
from prompt_enhancer import ResearchMode, enhance_prompt


class TaskRepository(Protocol):
    async def enqueue(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        *,
        attachments: list[dict] | None = None,
        execution_mode: str | None = None,
        status_message_id: int | None = None,
    ) -> tuple[Any, int]: ...
    async def list_active(self, owner_id: str) -> list[Any]: ...
    async def latest_active_for_owner(self, owner_id: str) -> Any | None: ...
    async def cancel(self, task_id: int, owner_id: str) -> Any | None: ...
    async def cancel_running_for_owner(self, owner_id: str) -> Any | None: ...


class AttachmentCleanupPort(Protocol):
    def delete_input_records(self, records: Any) -> int: ...


@dataclass(frozen=True)
class QueuedRequest:
    task: Any
    queue_position: int
    enhanced: Any


@dataclass(frozen=True)
class AbortResult:
    cancelled_task: Any | None
    agent_stopped: bool


class TaskApplicationService:
    def __init__(
        self,
        repository: TaskRepository,
        agent_service: Any,
        attachment_store: AttachmentCleanupPort,
        guard: RequestGuard,
    ) -> None:
        self.repository = repository
        self.agent_service = agent_service
        self.attachment_store = attachment_store
        self.guard = guard

    def inspect_prompt(
        self,
        prompt: str,
        execution_mode: ResearchMode | None = None,
    ) -> Any:
        return enhance_prompt(prompt, requested_mode=execution_mode)

    def ensure_allowed(self, prompt: str) -> None:
        self.guard.ensure_allowed(prompt)

    async def enqueue_prompt(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        *,
        status_message_id: int | None = None,
        execution_mode: ResearchMode | None = None,
    ) -> QueuedRequest:
        self.guard.ensure_allowed(prompt)
        enhanced = enhance_prompt(prompt, requested_mode=execution_mode)
        task, position = await self.repository.enqueue(
            owner_id,
            chat_id,
            prompt,
            execution_mode=execution_mode.value if execution_mode is not None else None,
            status_message_id=status_message_id,
        )
        return QueuedRequest(task=task, queue_position=position, enhanced=enhanced)

    async def active(self, owner_id: str) -> list[Any]:
        return await self.repository.list_active(owner_id)

    async def abort_running(self, owner_id: str) -> AbortResult:
        cancelled = await self.repository.cancel_running_for_owner(owner_id)
        stopped = await self.agent_service.abort_current(owner_id)
        return AbortResult(cancelled_task=cancelled, agent_stopped=stopped)

    async def cancel_current(self, owner_id: str) -> Any | None:
        target = await self.repository.latest_active_for_owner(owner_id)
        if target is None:
            return None
        cancelled = await self.repository.cancel(target.id, owner_id)
        if cancelled is None:
            return None
        if target.status == "running":
            await self.agent_service.abort_current(owner_id)
        elif target.attachments:
            self.attachment_store.delete_input_records(target.attachments)
        return cancelled
