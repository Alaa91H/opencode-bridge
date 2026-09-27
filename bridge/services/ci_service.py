"""Application service for GitHub CI visibility."""

from __future__ import annotations

from typing import Any, Protocol


class CIClientPort(Protocol):
    async def latest_run(
        self,
        repo_slug: str,
        *,
        branch: str | None = None,
        workflow: str | None = None,
    ) -> Any: ...


class CIStatusService:
    def __init__(self, workspace_service: Any, client: CIClientPort, workflow: str | None) -> None:
        self.workspace_service = workspace_service
        self.client = client
        self.workflow = workflow

    async def latest(self, owner_id: str) -> tuple[Any, Any]:
        repo = await self.workspace_service.status(owner_id)
        status = await self.client.latest_run(
            repo.slug,
            branch=repo.branch,
            workflow=self.workflow,
        )
        return repo, status
