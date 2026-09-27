from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from bridge.domain.policies import RequestGuard
from bridge.services.workspace_service import WorkspaceService, WorkspaceUnavailable, workspace_prompt


@dataclass
class Active:
    owner_id: str
    repo_slug: str
    directory: str


class FakeStore:
    def __init__(self) -> None:
        self.items: dict[str, Active] = {}

    async def get(self, owner_id: str):
        return self.items.get(owner_id)

    async def set(self, owner_id: str, repo_slug: str, directory: str):
        item = Active(owner_id, repo_slug, directory)
        self.items[owner_id] = item
        return item


class FakeManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.allowed = ("Alaa91H/opencode-bridge", "Alaa91H/Other")
        for slug in self.allowed:
            self.repo_path(slug).mkdir(parents=True, exist_ok=True)

    def require_allowed(self, value: str) -> str:
        for slug in self.allowed:
            if slug.casefold() == value.casefold():
                return slug
        raise RuntimeError("not allowed")

    def repo_path(self, value: str) -> Path:
        slug = value
        owner, repo = slug.split("/", 1)
        return (self.root / owner / repo).resolve()

    def configured_repos(self):
        return self.allowed

    def local_repos(self):
        return [self.allowed[0]]

    async def ensure_repo(self, value: str, sync: bool = True):
        slug = self.require_allowed(value)
        return SimpleNamespace(
            slug=slug,
            directory=self.repo_path(slug),
            branch="main",
            dirty=False,
            summary="## main",
        )

    async def status(self, value: str):
        slug = self.require_allowed(value)
        return SimpleNamespace(
            slug=slug,
            directory=self.repo_path(slug),
            branch="main",
            dirty=False,
            summary="## main",
        )

    async def sync_repo(self, value: str):
        return await self.status(value)


class FakeAgent:
    def __init__(self) -> None:
        self.fresh: list[str] = []

    async def fresh_session(self, owner_id: str):
        self.fresh.append(owner_id)
        return "session"


class FakeTasks:
    def __init__(self) -> None:
        self.calls = []

    async def enqueue_prompt(self, owner_id, chat_id, prompt, **kwargs):
        self.calls.append((owner_id, chat_id, prompt, kwargs))
        task = SimpleNamespace(id=7)
        return SimpleNamespace(task=task, queue_position=1)


class WorkspaceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = FakeStore()
        self.manager = FakeManager(root)
        self.agent = FakeAgent()
        self.tasks = FakeTasks()
        self.service = WorkspaceService(
            self.manager,
            self.store,
            self.agent,
            self.tasks,
            RequestGuard((lambda prompt: None,)),
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_select_persists_workspace_and_refreshes_agent_session(self) -> None:
        status = await self.service.select("42", "Alaa91H/opencode-bridge")
        self.assertEqual(status.slug, "Alaa91H/opencode-bridge")
        self.assertEqual(self.agent.fresh, ["42"])
        active = await self.store.get("42")
        self.assertEqual(active.repo_slug, "Alaa91H/opencode-bridge")

    async def test_active_rejects_tampered_saved_directory(self) -> None:
        self.store.items["42"] = Active(
            "42",
            "Alaa91H/opencode-bridge",
            str(Path(self.temp.name) / "wrong"),
        )
        with self.assertRaises(WorkspaceUnavailable):
            await self.service.active("42")

    async def test_queue_validates_user_text_but_stores_trusted_workspace_context(self) -> None:
        await self.service.select("42", "Alaa91H/opencode-bridge")
        queued = await self.service.queue("42", 100, "fix the tests", status_message_id=9)
        self.assertEqual(queued.repo_slug, "Alaa91H/opencode-bridge")
        _, _, original_prompt, kwargs = self.tasks.calls[-1]
        self.assertEqual(original_prompt, "fix the tests")
        stored = kwargs["stored_prompt"]
        self.assertIn("ACTIVE_WORKSPACE (trusted bridge context)", stored)
        self.assertIn("repository: Alaa91H/opencode-bridge", stored)
        self.assertIn("USER_REQUEST:\nfix the tests", stored)
        self.assertEqual(kwargs["status_message_id"], 9)

    async def test_list_marks_local_and_active_without_telegram_state(self) -> None:
        await self.service.select("42", "Alaa91H/opencode-bridge")
        items = await self.service.list("42")
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].local)
        self.assertTrue(items[0].active)
        self.assertFalse(items[1].local)
        self.assertFalse(items[1].active)

    def test_workspace_prompt_is_deterministic(self) -> None:
        prompt = workspace_prompt("owner/repo", "/srv/repo", "do work")
        self.assertIn("repository: owner/repo", prompt)
        self.assertIn("directory: /srv/repo", prompt)
        self.assertTrue(prompt.endswith("USER_REQUEST:\ndo work"))


if __name__ == "__main__":
    unittest.main()
