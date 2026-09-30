"""Per-task Git workspace isolation with exclusive repository/task locks."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class WorkspacePolicy:
    strategy: Literal["worktree", "clone"] = "worktree"
    retain_failed: bool = True
    allow_commit: bool = True
    allow_push: bool = False
    allow_pr: bool = False


_DEFAULT_WORKSPACEPOLICY = WorkspacePolicy()  # frozen config: safe to share as a default


@dataclass(frozen=True)
class Workspace:
    task_id: str
    path: Path
    repository: Path
    strategy: str


class WorkspaceBusy(RuntimeError):
    pass


class WorkspaceManager:
    def __init__(self, root: Path, policy: WorkspacePolicy = _DEFAULT_WORKSPACEPOLICY) -> None:
        self.root = root.resolve()
        self.policy = policy
        self._locks: set[str] = set()
        self._guard = asyncio.Lock()

    async def acquire(self, task_id: str, repository: Path) -> Workspace:
        repo = repository.resolve()
        key = f"{repo}:{task_id}"
        async with self._guard:
            if key in self._locks:
                raise WorkspaceBusy(key)
            self._locks.add(key)
        path = self.root / task_id
        try:
            if path.exists():
                raise WorkspaceBusy(f"workspace already exists: {path}")
            self.root.mkdir(parents=True, exist_ok=True)
            if self.policy.strategy == "worktree":
                await self._git(repo, "worktree", "add", "--detach", str(path), "HEAD")
            else:
                await self._git(repo.parent, "clone", "--no-hardlinks", str(repo), str(path))
            return Workspace(task_id, path, repo, self.policy.strategy)
        except BaseException:
            async with self._guard:
                self._locks.discard(key)
            raise

    async def release(self, workspace: Workspace, *, failed: bool = False) -> None:
        key = f"{workspace.repository}:{workspace.task_id}"
        try:
            if failed and self.policy.retain_failed:
                return
            if workspace.strategy == "worktree":
                await self._git(workspace.repository, "worktree", "remove", "--force", str(workspace.path))
            elif workspace.path.exists():
                shutil.rmtree(workspace.path)
        finally:
            async with self._guard:
                self._locks.discard(key)

    async def commit(self, workspace: Workspace, message: str) -> None:
        if not self.policy.allow_commit:
            raise PermissionError("commit disabled by workspace policy")
        await self._git(workspace.path, "add", "-A")
        await self._git(workspace.path, "commit", "-m", message)

    async def push(self, workspace: Workspace, remote: str, branch: str) -> None:
        if not self.policy.allow_push:
            raise PermissionError("push disabled by workspace policy")
        await self._git(workspace.path, "push", remote, f"HEAD:{branch}")

    def pr_allowed(self) -> bool:
        return self.policy.allow_pr

    @staticmethod
    async def _git(cwd: Path, *args: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "git command failed")
