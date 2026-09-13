"""Workspace-aware OpenCode runtime isolation for V3.

The base Telegram bridge intentionally remains repository-agnostic.  This module
layers project isolation on top by binding every OpenCode HTTP request executed
for a V3 task to the trusted workspace directory that was captured when the
queue item was created.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx

from opencode_client import OpenCodeClient
from workspace_manager import GitWorkspaceManager, WorkspaceError

_WORKSPACE_PREFIX = "ACTIVE_WORKSPACE (trusted bridge context)"
_CURRENT_DIRECTORY: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "opencode_workspace_directory", default=None
)


class WorkspaceOpenCodeClient(OpenCodeClient):
    """OpenCode client that injects the workspace directory per async task.

    ``ContextVar`` keeps concurrent workers isolated.  A small in-memory session
    registry also lets session-specific requests retain their directory when a
    later call happens outside the original task context.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session_directories: dict[str, str] = {}
        self._client.event_hooks["request"].append(self._inject_directory)

    @staticmethod
    def _session_id_from_path(path: str) -> str | None:
        parts = [part for part in path.split("/") if part]
        try:
            index = parts.index("session")
        except ValueError:
            return None
        if index + 1 >= len(parts):
            return None
        candidate = parts[index + 1]
        if candidate in {"status"}:
            return None
        return candidate or None

    async def _inject_directory(self, request: httpx.Request) -> None:
        directory = _CURRENT_DIRECTORY.get()
        if directory is None:
            session_id = self._session_id_from_path(request.url.path)
            if session_id:
                directory = self._session_directories.get(session_id)
        if directory:
            request.headers["x-opencode-directory"] = directory

    @contextmanager
    def directory_scope(self, directory: str | Path) -> Iterator[str]:
        resolved = str(Path(directory).expanduser().resolve())
        token = _CURRENT_DIRECTORY.set(resolved)
        try:
            yield resolved
        finally:
            _CURRENT_DIRECTORY.reset(token)

    async def create_session(
        self,
        parent_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        payload = await super().create_session(parent_id=parent_id, title=title)
        directory = _CURRENT_DIRECTORY.get()
        session_id = payload.get("id") or payload.get("sessionId")
        if not session_id and isinstance(payload.get("session"), dict):
            session_id = payload["session"].get("id")
        if directory and isinstance(session_id, str) and session_id:
            self._session_directories[session_id] = directory
        return payload

    async def delete_session(self, session_id: str) -> bool:
        deleted = await super().delete_session(session_id)
        if deleted:
            self._session_directories.pop(session_id, None)
        return deleted


def workspace_from_prompt(prompt: str, manager: GitWorkspaceManager) -> tuple[str, Path] | None:
    """Extract and validate only the bridge-generated trusted workspace block."""
    lines = prompt.splitlines()
    if not lines or lines[0] != _WORKSPACE_PREFIX:
        return None
    if len(lines) < 5 or not lines[1].startswith("repository: ") or not lines[2].startswith("directory: "):
        raise WorkspaceError("trusted workspace context is malformed")
    if lines[4] != "END_ACTIVE_WORKSPACE":
        raise WorkspaceError("trusted workspace context terminator is missing")

    slug = manager.require_allowed(lines[1].removeprefix("repository: ").strip())
    expected = manager.repo_path(slug)
    supplied = Path(lines[2].removeprefix("directory: ").strip()).expanduser().resolve()
    if supplied != expected:
        raise WorkspaceError("trusted workspace directory does not match the allowed repository")
    return slug, expected


async def install(core: Any, workspace_store: Any, manager: GitWorkspaceManager) -> None:
    """Promote the V3 runtime to a workspace-scoped OpenCode client."""
    if isinstance(core.client, WorkspaceOpenCodeClient):
        return

    old_client = core.client
    scoped_client = WorkspaceOpenCodeClient(
        host=core.OPENCODE_HOST,
        port=core.OPENCODE_PORT,
        password=core.OPENCODE_PASSWORD,
    )
    core.client = scoped_client
    await old_client.close()

    original_create_fresh_session = core._create_fresh_session
    original_execute_agent_task = core._execute_agent_task
    session_bindings: dict[str, tuple[str, str]] = {}

    async def create_fresh_session(user_id: str) -> str:
        active = await workspace_store.get(user_id)
        if active is None:
            session_bindings.pop(user_id, None)
            return await original_create_fresh_session(user_id)

        slug = manager.require_allowed(active.repo_slug)
        expected = manager.repo_path(slug)
        if expected != Path(active.directory).expanduser().resolve():
            raise WorkspaceError("saved workspace directory no longer matches repository policy")
        with scoped_client.directory_scope(expected) as directory:
            session_id = await original_create_fresh_session(user_id)
        session_bindings[user_id] = (directory, session_id)
        return session_id

    async def execute_agent_task(task: Any, bot: Any) -> None:
        workspace = workspace_from_prompt(task.prompt, manager)
        if workspace is None:
            await original_execute_agent_task(task, bot)
            return

        _, directory_path = workspace
        directory = str(directory_path)
        with scoped_client.directory_scope(directory):
            current = await core.store.get_session(task.owner_id)
            binding = session_bindings.get(task.owner_id)
            current_id = current.opencode_session_id if current else None
            if binding != (directory, current_id):
                session_id = await original_create_fresh_session(task.owner_id)
                session_bindings[task.owner_id] = (directory, session_id)
            await original_execute_agent_task(task, bot)

    core._create_fresh_session = create_fresh_session
    core._execute_agent_task = execute_agent_task
