"""Application-level task use cases, separate from worker execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from bridge.domain.policies import RequestGuard
from prompt_enhancer import ResearchMode, enhance_prompt


class TaskRepository(Protocol):
    async def get(self, task_id: int) -> Any | None: ...
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
    async def enqueue_once(
        self, scope: str, idempotency_key: str, owner_id: str, chat_id: int, prompt: str, **kwargs: Any
    ) -> tuple[Any, int, bool]: ...
    async def list_active(self, owner_id: str) -> list[Any]: ...
    async def latest_active_for_owner(self, owner_id: str) -> Any | None: ...
    async def cancel(self, task_id: int, owner_id: str) -> Any | None: ...
    async def cancel_running_for_owner(self, owner_id: str) -> Any | None: ...
    async def delete_user_task(self, task_id: int, owner_id: str) -> Any | None: ...
    async def change_priority(self, task_id: int, owner_id: str, delta: int) -> Any | None: ...
    async def move_waiting(self, task_id: int, owner_id: str, offset: int) -> Any | None: ...
    async def update_waiting_prompt(
        self, task_id: int, owner_id: str, prompt: str,
    ) -> Any | None: ...
    async def list_waiting(self, owner_id: str, limit: int = 50) -> list[Any]: ...
    async def delete_waiting(self, owner_id: str) -> list[Any]: ...
    async def list_recent(self, owner_id: str, limit: int = 10) -> list[Any]: ...
    async def list_failed(self, owner_id: str, limit: int = 20) -> list[Any]: ...
    async def retry_failed(self, task_id: int, owner_id: str) -> Any | None: ...
    async def save_checkpoint(self, task_id: int, checkpoint: dict[str, Any]) -> None: ...


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
        stored_prompt: str | None = None,
        direct_dispatch: bool = False,
        daily_continuation: bool = False,
        idempotency_scope: str | None = None,
        idempotency_key: str | None = None,
    ) -> QueuedRequest:
        """Validate the user instruction while allowing trusted bridge context in storage.

        `stored_prompt` is reserved for bridge-generated context such as a verified
        workspace header. Policy checks and prompt classification always use the
        original user instruction so trusted metadata cannot accidentally change
        request-policy semantics.
        """
        self.guard.ensure_allowed(prompt)
        enhanced = enhance_prompt(prompt, requested_mode=execution_mode)
        enqueue_kwargs = {
            "execution_mode": execution_mode.value if execution_mode is not None else None,
            "status_message_id": status_message_id,
        }
        stored = stored_prompt if stored_prompt is not None else prompt
        if idempotency_scope and idempotency_key:
            task, position, _created = await self.repository.enqueue_once(
                idempotency_scope, idempotency_key, owner_id, chat_id, stored, **enqueue_kwargs
            )
        else:
            task, position = await self.repository.enqueue(owner_id, chat_id, stored, **enqueue_kwargs)
        if direct_dispatch or daily_continuation:
            # Queue checkpoint is committed before wake_tasks() starts a worker.
            await self.save_checkpoint(
                task,
                {
                    "direct_dispatch": bool(direct_dispatch),
                    "daily_continuation": bool(daily_continuation),
                },
            )
        return QueuedRequest(task=task, queue_position=position, enhanced=enhanced)

    async def save_checkpoint(self, task: Any, updates: dict[str, Any]) -> None:
        """Merge ``updates`` into a queued task checkpoint through the repository port.

        Adapters must not reach into ``repository`` directly, so dispatch
        semantics travel through this method instead.
        """
        persisted = getattr(task, "checkpoint", None) or {}
        await self.repository.save_checkpoint(task.id, {**dict(persisted), **updates})

    async def active(self, owner_id: str) -> list[Any]:
        return await self.repository.list_active(owner_id)

    async def change_priority(self, owner_id: str, task_id: int, delta: int) -> Any | None:
        return await self.repository.change_priority(task_id, owner_id, delta)

    async def move_waiting(self, owner_id: str, task_id: int, offset: int) -> Any | None:
        return await self.repository.move_waiting(task_id, owner_id, offset)

    async def edit_waiting_prompt(
        self, owner_id: str, task_id: int, prompt: str,
    ) -> Any | None:
        self.guard.ensure_allowed(prompt)
        return await self.repository.update_waiting_prompt(task_id, owner_id, prompt)

    async def waiting(self, owner_id: str, limit: int = 50) -> list[Any]:
        return await self.repository.list_waiting(owner_id, limit)

    async def queue_snapshot(self, owner_id: str) -> tuple[Any | None, list[Any]]:
        """The running task (if any) and the waiting tasks in claim order."""
        active = await self.repository.list_active(owner_id)
        running = next(
            (task for task in active if task.status in {"running", "leased"}),
            None,
        )
        return running, await self.repository.list_waiting(owner_id)

    async def clear_waiting(self, owner_id: str) -> list[Any]:
        removed = await self.repository.delete_waiting(owner_id)
        for task in removed:
            if getattr(task, "attachments", None):
                self.attachment_store.delete_input_records(task.attachments)
        return removed

    async def remove(self, owner_id: str, task_id: int) -> Any | None:
        deleted = await self.repository.delete_user_task(task_id, owner_id)
        if deleted is not None and getattr(deleted, "attachments", None):
            self.attachment_store.delete_input_records(deleted.attachments)
        return deleted

    async def recent(self, owner_id: str) -> list[Any]:
        return await self.repository.list_recent(owner_id)

    async def latest_active(self, owner_id: str) -> Any | None:
        return await self.repository.latest_active_for_owner(owner_id)

    async def failed(self, owner_id: str, limit: int = 20) -> list[Any]:
        return await self.repository.list_failed(owner_id, limit)

    async def retry_failed(self, owner_id: str, task_id: int) -> Any | None:
        return await self.repository.retry_failed(task_id, owner_id)

    async def abort_running(self, owner_id: str) -> AbortResult:
        cancelled = await self.repository.cancel_running_for_owner(owner_id)
        stopped = await self.agent_service.abort_current(owner_id)
        return AbortResult(cancelled_task=cancelled, agent_stopped=stopped)

    async def cancel_current(self, owner_id: str) -> Any | None:
        target = await self.repository.latest_active_for_owner(owner_id)
        if target is None:
            return None
        return await self._cancel_target(target, owner_id)

    async def cancel(self, task_id: int, owner_id: str) -> Any | None:
        target = await self.repository.get(task_id)
        if target is None or target.owner_id != owner_id:
            return None
        return await self._cancel_target(target, owner_id)

    async def _cancel_target(self, target: Any, owner_id: str) -> Any | None:
        previous_status = target.status
        cancelled = await self.repository.cancel(target.id, owner_id)
        if cancelled is None:
            return None
        if previous_status in {"running", "leased", "retrying"}:
            checkpoint = getattr(target, "checkpoint", None) or {}
            if checkpoint.get("session_id"):
                await self.agent_service.client.abort_session(checkpoint["session_id"])
            else:
                await self.agent_service.abort_current(owner_id)
        if previous_status != "running" and target.attachments:
            self.attachment_store.delete_input_records(target.attachments)
        return cancelled
