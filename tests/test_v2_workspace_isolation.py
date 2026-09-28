import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bridge.infrastructure.workspaces.manager import Workspace, WorkspaceBusy, WorkspaceManager, WorkspacePolicy


class WorkspaceIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_task_repository_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ws"
            repo = Path(td) / "repo"
            repo.mkdir()
            manager = WorkspaceManager(root)
            async def fake_git(cwd, *args):
                (root / "task").mkdir(parents=True)
            with patch.object(manager, "_git", side_effect=fake_git):
                first = await manager.acquire("task", repo)
                with self.assertRaises(WorkspaceBusy):
                    await manager.acquire("task", repo)
                await manager.release(first)

    async def test_distinct_tasks_receive_distinct_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ws"
            repo = Path(td) / "repo"; repo.mkdir()
            manager = WorkspaceManager(root)
            async def fake_git(cwd, *args):
                target = Path(args[-2]) if args[0] == "worktree" and args[1] == "add" else None
                if target: target.mkdir(parents=True, exist_ok=True)
            with patch.object(manager, "_git", side_effect=fake_git):
                a = await manager.acquire("a", repo)
                b = await manager.acquire("b", repo)
                self.assertNotEqual(a.path, b.path)

    async def test_failed_workspace_can_be_retained_for_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "task"; path.mkdir()
            repo = Path(td) / "repo"; repo.mkdir()
            manager = WorkspaceManager(Path(td), WorkspacePolicy(retain_failed=True))
            workspace = Workspace("task", path, repo.resolve(), "worktree")
            await manager.release(workspace, failed=True)
            self.assertTrue(path.exists())

    async def test_push_and_pr_are_denied_by_default(self):
        workspace = Workspace("t", Path("/tmp/t"), Path("/tmp/r"), "worktree")
        manager = WorkspaceManager(Path("/tmp/ws"))
        with self.assertRaises(PermissionError):
            await manager.push(workspace, "origin", "branch")
        self.assertFalse(manager.pr_allowed())

    async def test_clone_cleanup_removes_only_task_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            repo = Path(td) / "repo"; repo.mkdir()
            task = root / "task"; task.mkdir()
            marker = repo / "keep"; marker.write_text("safe")
            manager = WorkspaceManager(root, WorkspacePolicy(strategy="clone"))
            await manager.release(Workspace("task", task, repo.resolve(), "clone"))
            self.assertFalse(task.exists())
            self.assertTrue(marker.exists())
