"""Regression tests for GitHub administration (service, store and /gh adapter)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.services.github_admin_service import (
    GitHubAdminError,
    GitHubAdminService,
)
from bridge.services.managed_repo_store import ManagedRepoStore
from bridge.telegram.commands.github import GitHubCommands


class FakeRunner:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[list[str]] = []

    async def run(self, args, *, timeout=None) -> str:
        self.calls.append([str(part) for part in args])
        if not self.responses:
            raise AssertionError(f"unexpected gh call: {args}")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeStore:
    def __init__(self, slugs: tuple[str, ...] = ()) -> None:
        self.slugs = {slug.casefold(): slug for slug in slugs}

    async def add(self, slug: str) -> None:
        self.slugs.setdefault(slug.casefold(), slug)

    async def remove(self, slug: str) -> bool:
        return self.slugs.pop(slug.casefold(), None) is not None

    async def list(self) -> tuple[str, ...]:
        return tuple(self.slugs.values())


def _service(runner: FakeRunner, *, static=(), store=None, allowed=None, disallowed=None):
    if store is None:
        store = FakeStore()
    allowed = allowed if allowed is not None else []
    disallowed = disallowed if disallowed is not None else []
    return GitHubAdminService(
        runner,
        static_repos=static,
        store=store,
        on_allow=allowed.append,
        on_disallow=disallowed.append,
    )


class GitHubServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_repo_view_parses_default_branch(self) -> None:
        runner = FakeRunner([
            '{"nameWithOwner":"Alaa91H/opencode-bridge","description":"bridge",'
            '"visibility":"PUBLIC","defaultBranchRef":{"name":"main"},'
            '"url":"https://github.com/Alaa91H/opencode-bridge","pushedAt":"2026-10-23T00:00:00Z"}'
        ])
        repo = await _service(runner).repo("Alaa91H/opencode-bridge")
        self.assertEqual(repo.slug, "Alaa91H/opencode-bridge")
        self.assertEqual(repo.default_branch, "main")
        self.assertEqual(repo.visibility, "PUBLIC")

    async def test_add_repo_canonicalises_and_extends_allowlist(self) -> None:
        allowed: list[str] = []
        store = FakeStore()
        runner = FakeRunner(['{"nameWithOwner":"Alaa91H/QuranLiveStream"}'])
        service = _service(runner, store=store, allowed=allowed)
        canonical = await service.add_repo("alaa91h/quranlivestream")
        self.assertEqual(canonical, "Alaa91H/QuranLiveStream")
        self.assertEqual(allowed, ["Alaa91H/QuranLiveStream"])
        self.assertTrue(await service.is_managed("Alaa91H/QuranLiveStream"))

    async def test_static_repo_cannot_be_removed_from_chat(self) -> None:
        service = _service(FakeRunner([]), static=("Alaa91H/opencode-bridge",))
        with self.assertRaises(GitHubAdminError):
            await service.remove_repo("Alaa91H/opencode-bridge")

    async def test_remove_drops_managed_repo_and_allowlist(self) -> None:
        disallowed: list[str] = []
        store = FakeStore(("Alaa91H/WA-X",))
        service = _service(FakeRunner([]), store=store, disallowed=disallowed)
        self.assertTrue(await service.remove_repo("alaa91h/wa-x"))
        self.assertEqual(disallowed, ["alaa91h/wa-x"])
        self.assertFalse(await service.is_managed("Alaa91H/WA-X"))

    async def test_create_repo_records_new_repository(self) -> None:
        runner = FakeRunner([
            "https://github.com/Alaa91H/fresh-repo\n",
            "Alaa91H\n",
            '{"nameWithOwner":"Alaa91H/fresh-repo"}',
            '{"nameWithOwner":"Alaa91H/fresh-repo","description":"",'
            '"visibility":"PRIVATE","defaultBranchRef":null,"url":"https://github.com/x","pushedAt":""}',
        ])
        store = FakeStore()
        service = _service(runner, store=store)
        repo = await service.create_repo("fresh-repo", private=True)
        self.assertEqual(repo.slug, "Alaa91H/fresh-repo")
        self.assertIn("--private", runner.calls[0])
        self.assertTrue(await store.list())
        self.assertEqual(repo.default_branch, "")

    async def test_create_repo_rejects_invalid_name_before_calling_gh(self) -> None:
        runner = FakeRunner([])
        with self.assertRaises(GitHubAdminError):
            await _service(runner).create_repo("bad name!")
        self.assertEqual(runner.calls, [])

    async def test_pulls_parse_author_and_state(self) -> None:
        runner = FakeRunner([
            '[{"number":30,"title":"deps","state":"OPEN","headRefName":"h","baseRefName":"main",'
            '"isDraft":false,"url":"u","author":{"login":"app/dependabot"},"updatedAt":"t"}]'
        ])
        pulls = await _service(runner).pulls("Alaa91H/opencode-bridge", state="open")
        self.assertEqual(pulls[0].author, "app/dependabot")
        self.assertEqual(pulls[0].number, 30)
        self.assertIn("--state", runner.calls[0])
        self.assertIn("open", runner.calls[0])

    async def test_create_pull_requires_managed_repo(self) -> None:
        runner = FakeRunner([])
        service = _service(runner, store=FakeStore())
        with self.assertRaises(GitHubAdminError):
            await service.create_pull("Alaa91H/other", title="t", head="h", base="main")
        self.assertEqual(runner.calls, [])

    async def test_create_pull_passes_draft_and_body(self) -> None:
        runner = FakeRunner(["https://github.com/Alaa91H/opencode-bridge/pull/99\n"])
        service = _service(runner, store=FakeStore(("Alaa91H/opencode-bridge",)))
        url = await service.create_pull(
            "Alaa91H/opencode-bridge",
            title="feat: x",
            head="feature/x",
            base="main",
            body="details",
            draft=True,
        )
        self.assertIn("/pull/99", url)
        args = runner.calls[0]
        self.assertIn("--draft", args)
        self.assertEqual(args[args.index("--head") + 1], "feature/x")

    async def test_merge_rejects_unknown_method(self) -> None:
        service = _service(FakeRunner([]), store=FakeStore(("a/b",)))
        with self.assertRaises(GitHubAdminError):
            await service.merge_pull("a/b", 1, method="fast")

    async def test_merge_uses_squash(self) -> None:
        runner = FakeRunner(["merged\n"])
        service = _service(runner, store=FakeStore(("Alaa91H/opencode-bridge",)))
        await service.merge_pull("Alaa91H/opencode-bridge", 30, method="squash")
        self.assertEqual(runner.calls[0][:6], ["pr", "merge", "30", "--repo", "Alaa91H/opencode-bridge", "--squash"])

    async def test_branches_and_releases_parse(self) -> None:
        runner = FakeRunner(["main\nfeature/x\n", '[{"tagName":"v1","name":"One","isDraft":false,'
                                                  '"isPrerelease":false,"publishedAt":"p","url":"u"}]'])
        service = _service(runner)
        self.assertEqual(await service.branches("Alaa91H/opencode-bridge"), ("main", "feature/x"))
        releases = await service.releases("Alaa91H/opencode-bridge")
        self.assertEqual(releases[0].tag, "v1")

    async def test_issue_and_comment_require_managed_repo(self) -> None:
        service = _service(FakeRunner([]), store=FakeStore())
        with self.assertRaises(GitHubAdminError):
            await service.create_issue("a/b", title="bug")
        with self.assertRaises(GitHubAdminError):
            await service.comment("a/b", 1, "text")


class PushAccountingTests(unittest.IsolatedAsyncioTestCase):
    class _GitService(GitHubAdminService):
        def __init__(self, outputs: dict[str, str]) -> None:
            super().__init__(FakeRunner([]))
            self.outputs = outputs
            self.git_calls: list[tuple[str, ...]] = []

        async def _git(self, cwd, *args):  # type: ignore[override]
            self.git_calls.append(tuple(args))
            return self.outputs.get(" ".join(args), "")

    def workspace(self) -> Path:
        directory = Path(tempfile.mkdtemp()) / "repo"
        (directory / ".git").mkdir(parents=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(directory.parent, ignore_errors=True))
        return directory

    def _outputs(self, name_status: str) -> dict[str, str]:
        return {
            "branch --show-current": "main\n",
            "status --porcelain=v1": " M src/app.py\n",
            "rev-parse HEAD": "a" * 40 + "\n",
            "diff --cached --name-status": name_status,
            "diff --cached --unified=0": "+value = 1\n",
        }

    async def test_push_commits_and_pushes_changes(self) -> None:
        service = self._GitService(self._outputs("M\tsrc/app.py\n"))
        result = await service.push(self.workspace(), "fix: x")
        self.assertTrue(result.changed)
        self.assertEqual(result.branch, "main")
        self.assertIn(("push", "origin", "main"), service.git_calls)
        self.assertIn(("commit", "-m", "fix: x"), service.git_calls)

    async def test_push_refuses_env_file(self) -> None:
        service = self._GitService(self._outputs("A\t.env\n"))
        with self.assertRaises(GitHubAdminError):
            await service.push(self.workspace(), "chore: x")
        self.assertNotIn(("commit", "-m", "chore: x"), service.git_calls)

    async def test_push_allows_documented_env_example(self) -> None:
        service = self._GitService(self._outputs("M\t.env.example\n"))
        result = await service.push(self.workspace(), "docs: env")
        self.assertTrue(result.changed)

    async def test_push_refuses_private_key_content(self) -> None:
        outputs = self._outputs("M\tsrc/app.py\n")
        outputs["diff --cached --unified=0"] = "+-----BEGIN RSA PRIVATE KEY-----\n"
        service = self._GitService(outputs)
        with self.assertRaises(GitHubAdminError):
            await service.push(self.workspace(), "chore: x")

    async def test_push_without_changes_is_a_noop(self) -> None:
        outputs = self._outputs("")
        outputs["status --porcelain=v1"] = ""
        service = self._GitService(outputs)
        result = await service.push(self.workspace(), "fix: x")
        self.assertFalse(result.changed)
        self.assertNotIn(("push", "origin", "main"), service.git_calls)

    async def test_push_requires_message(self) -> None:
        service = self._GitService(self._outputs(""))
        with self.assertRaises(GitHubAdminError):
            await service.push(self.workspace(), "  ")


class ManagedRepoStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_list_remove_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = BridgeDatabase(Path(tmp) / "sessions.db")
            store = ManagedRepoStore(Path(tmp) / "sessions.db", database=database)
            await store.init()
            await store.add("Alaa91H/One")
            await store.add("Alaa91H/one")
            self.assertEqual(await store.list(), ("Alaa91H/One",))
            self.assertTrue(await store.remove("alaa91h/one"))
            self.assertEqual(await store.list(), ())
            await database.close()


class GitHubCommandTests(unittest.IsolatedAsyncioTestCase):
    def _update(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        return SimpleNamespace(effective_user=SimpleNamespace(id=1), message=message)

    def _commands(self, service, workspace=None):
        self.replies: list[str] = []

        async def reply(message, text):
            self.replies.append(text)

        if workspace is None:
            workspace = SimpleNamespace(
                active=AsyncMock(return_value=None),
                select=AsyncMock(),
                sync=AsyncMock(),
            )
        return GitHubCommands(service, workspace, reply=reply, audit_write=lambda *a, **k: None)

    async def test_help_lists_push_and_pull_requests(self) -> None:
        commands = self._commands(SimpleNamespace())
        await commands.handle(self._update(), SimpleNamespace(args=[]))
        self.assertIn("/gh push", self.replies[0])
        self.assertIn("/gh prnew", self.replies[0])

    async def test_status_reports_login_and_managed_repos(self) -> None:
        service = SimpleNamespace(
            login=AsyncMock(return_value="Alaa91H"),
            managed_repos=AsyncMock(return_value=("Alaa91H/opencode-bridge",)),
        )
        commands = self._commands(service)
        await commands.handle(self._update(), SimpleNamespace(args=["status"]))
        self.assertIn("Alaa91H", self.replies[0])
        self.assertIn("opencode-bridge", self.replies[0])

    async def test_prs_defaults_to_active_workspace(self) -> None:
        pull = SimpleNamespace(
            number=7, title="fix", state="OPEN", head="h", base="main",
            author="x", url="u", draft=False, updated_at="",
        )
        service = SimpleNamespace(pulls=AsyncMock(return_value=(pull,)))
        workspace = SimpleNamespace(
            active=AsyncMock(return_value=SimpleNamespace(repo_slug="Alaa91H/opencode-bridge")),
            select=AsyncMock(), sync=AsyncMock(),
        )
        commands = self._commands(service, workspace)
        await commands.handle(self._update(), SimpleNamespace(args=["prs"]))
        service.pulls.assert_awaited_once_with("Alaa91H/opencode-bridge", state="open")
        self.assertIn("#7", self.replies[0])

    async def test_prs_accepts_explicit_slug_and_state(self) -> None:
        service = SimpleNamespace(pulls=AsyncMock(return_value=()))
        commands = self._commands(service)
        await commands.handle(
            self._update(), SimpleNamespace(args=["prs", "a/b", "closed"])
        )
        service.pulls.assert_awaited_once_with("a/b", state="closed")

    async def test_push_requires_active_workspace(self) -> None:
        service = SimpleNamespace(push=AsyncMock())
        commands = self._commands(service)
        await commands.handle(self._update(), SimpleNamespace(args=["push", "msg"]))
        self.assertIn("ما في مستودع نشط", self.replies[0])
        service.push.assert_not_awaited()

    async def test_known_service_error_is_reported(self) -> None:
        service = SimpleNamespace(
            add_repo=AsyncMock(side_effect=GitHubAdminError("مرفوض")),
        )
        commands = self._commands(service)
        await commands.handle(self._update(), SimpleNamespace(args=["add", "a/b"]))
        self.assertIn("مرفوض", self.replies[0])


if __name__ == "__main__":
    unittest.main()
