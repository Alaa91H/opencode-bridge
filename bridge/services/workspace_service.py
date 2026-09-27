"""Workspace application service with no Telegram dependency."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bridge.domain.policies import RequestGuard


class WorkspaceRepository(Protocol):
    async def get(self, owner_id: str) -> Any | None: ...
    async def set(self, owner_id: str, repo_slug: str, directory: str) -> Any: ...


class WorkspaceManagerPort(Protocol):
    def require_allowed(self, value: str) -> str: ...
    def repo_path(self, value: str) -> Path: ...
    def configured_repos(self) -> tuple[str, ...]: ...
    def local_repos(self) -> list[str]: ...
    async def ensure_repo(self, value: str, sync: bool = True) -> Any: ...
    async def status(self, value: str) -> Any: ...
    async def sync_repo(self, value: str) -> Any: ...


class WorkspaceUnavailable(LookupError):
    pass


@dataclass(frozen=True)
class WorkspaceListItem:
    slug: str
    local: bool
    active: bool


@dataclass(frozen=True)
class QueuedWorkspaceTask:
    task: Any
    queue_position: int
    repo_slug: str


def workspace_prompt(repo_slug: str, directory: str, request: str) -> str:
    return (
        "ACTIVE_WORKSPACE (trusted bridge context)\n"
        f"repository: {repo_slug}\n"
        f"directory: {directory}\n"
        "policy: work only inside this repository; do not build or install dependencies locally.\n"
        "END_ACTIVE_WORKSPACE\n\n"
        f"USER_REQUEST:\n{request}"
    )


class WorkspaceService:
    def __init__(
        self,
        manager: WorkspaceManagerPort,
        repository: WorkspaceRepository,
        agent_service: Any,
        task_service: Any,
        guard: RequestGuard,
    ) -> None:
        self.manager = manager
        self.repository = repository
        self.agent_service = agent_service
        self.task_service = task_service
        self.guard = guard

    async def active(self, owner_id: str) -> Any | None:
        active = await self.repository.get(owner_id)
        if active is None:
            return None
        slug = self.manager.require_allowed(active.repo_slug)
        expected = self.manager.repo_path(slug)
        if expected != Path(active.directory).resolve():
            raise RuntimeError("مسار مساحة العمل المحفوظ لم يعد صالحًا")
        return active

    async def select(self, owner_id: str, repo: str) -> Any:
        status = await self.manager.ensure_repo(repo, sync=True)
        await self.repository.set(owner_id, status.slug, str(status.directory))
        await self.agent_service.fresh_session(owner_id)
        return status

    async def status(self, owner_id: str) -> Any:
        active = await self.active(owner_id)
        if active is None:
            raise WorkspaceUnavailable("ما في مشروع نشط. استخدم /use owner/repo.")
        return await self.manager.status(active.repo_slug)

    async def list(self, owner_id: str) -> tuple[WorkspaceListItem, ...]:
        active = await self.repository.get(owner_id)
        local = {slug.casefold() for slug in self.manager.local_repos()}
        configured = self.manager.configured_repos()
        items = []
        for slug in configured:
            items.append(
                WorkspaceListItem(
                    slug=slug,
                    local=slug.casefold() in local,
                    active=bool(active and active.repo_slug.casefold() == slug.casefold()),
                )
            )
        return tuple(items)

    async def sync(self, owner_id: str) -> Any:
        active = await self.active(owner_id)
        if active is None:
            raise WorkspaceUnavailable("اختَر مشروع أولًا باستخدام /use owner/repo")
        return await self.manager.sync_repo(active.repo_slug)

    async def queue(
        self,
        owner_id: str,
        chat_id: int,
        request: str,
        *,
        status_message_id: int | None = None,
    ) -> QueuedWorkspaceTask:
        self.guard.ensure_allowed(request)
        active = await self.active(owner_id)
        if active is None:
            raise WorkspaceUnavailable("اختَر مشروع أولًا باستخدام /use owner/repo")
        prepared = workspace_prompt(active.repo_slug, active.directory, request)
        queued = await self.task_service.enqueue_prompt(
            owner_id,
            chat_id,
            request,
            status_message_id=status_message_id,
            stored_prompt=prepared,
        )
        return QueuedWorkspaceTask(
            task=queued.task,
            queue_position=queued.queue_position,
            repo_slug=active.repo_slug,
        )
