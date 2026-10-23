"""Professional GitHub repository/account administration for the bridge owner.

Every operation runs through the authenticated ``gh`` CLI (or ``git`` for the
final push); this module never builds, installs dependencies or runs project
tests. Repository maintenance is analysis, editing, fixing and pushing — GitHub
Actions stays the build authority, so the control server is never turned into a
CI machine.

The static ``GITHUB_ALLOWED_REPOS`` configuration stays a floor that Telegram
cannot remove. Repositories added at runtime are persisted by
:class:`ManagedRepoStore` and mirrored into the workspace manager, so the owner
can take over a repository from the chat and immediately use it with ``/use``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bridge.services.managed_repo_store import ManagedRepoStore

GIT = "git"
PR_STATES = frozenset({"open", "closed", "all"})
MERGE_METHODS = frozenset({"squash", "merge", "rebase"})

_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_SECRET_FILES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "credentials*",
    "*.netrc",
    ".netrc",
)
_SECRET_FILE_EXCEPTIONS = (".env.example", ".env.sample", ".env.template")
_SECRET_CONTENT = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
)


class GitHubAdminError(RuntimeError):
    """A GitHub operation failed; the message is safe to show to the owner."""


class GhRunner(Protocol):
    async def run(
        self, args: Sequence[str], *, timeout: float | None = None
    ) -> str: ...


class SubprocessGhRunner:
    """Run ``gh`` without a shell, with the configured token and clean output."""

    def __init__(self, binary: str = "gh", *, token: str = "", timeout: float = 90.0) -> None:
        self.binary = binary
        self.token = token.strip()
        self.timeout = timeout

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.token:
            env["GH_TOKEN"] = self.token
        env["GH_PROMPT_DISABLED"] = "1"
        env["NO_COLOR"] = "1"
        env["GH_NO_UPDATE_NOTIFIER"] = "1"
        return env

    def _clean(self, value: str) -> str:
        text = (value or "").strip()
        if self.token:
            text = text.replace(self.token, "[REDACTED]")
        return _TOKEN_RE.sub("[REDACTED]", text)

    async def run(self, args: Sequence[str], *, timeout: float | None = None) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env(),
            )
        except FileNotFoundError as exc:
            raise GitHubAdminError("أداة gh غير مثبتة على الخادم") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout or self.timeout
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise GitHubAdminError("انتهت مهلة عملية GitHub قبل اكتمالها") from exc
        out = self._clean(stdout.decode("utf-8", errors="replace"))
        err = self._clean(stderr.decode("utf-8", errors="replace"))
        if process.returncode:
            detail = (err or out or "gh أعاد خطأ غير موصوف").splitlines()[-1]
            raise GitHubAdminError(f"تعذر تنفيذ عملية GitHub: {detail[:400]}")
        return out


@dataclass(frozen=True)
class RepoSummary:
    slug: str
    description: str
    visibility: str
    default_branch: str
    url: str
    pushed_at: str = ""


@dataclass(frozen=True)
class PullSummary:
    number: int
    title: str
    state: str
    head: str
    base: str
    author: str
    url: str
    draft: bool = False
    updated_at: str = ""


@dataclass(frozen=True)
class PullDetail:
    summary: PullSummary
    body: str
    mergeable: str
    additions: int
    deletions: int
    changed_files: int


@dataclass(frozen=True)
class IssueSummary:
    number: int
    title: str
    state: str
    author: str
    labels: tuple[str, ...]
    url: str
    updated_at: str = ""


@dataclass(frozen=True)
class ReleaseSummary:
    tag: str
    name: str
    draft: bool
    prerelease: bool
    published_at: str
    url: str = ""


@dataclass(frozen=True)
class PushResult:
    branch: str
    commit: str
    changed: bool
    message: str


def _slug(value: str) -> str:
    raw = (value or "").strip().removesuffix(".git").strip("/")
    if not _SLUG_RE.fullmatch(raw):
        raise GitHubAdminError("استخدم اسم مستودع بصيغة owner/repo")
    return raw


def _author(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("login") or value.get("name") or "")
    return str(value or "")


def _labels(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            result.append(name)
    return tuple(result)


class GitHubAdminService:
    def __init__(
        self,
        runner: GhRunner,
        *,
        static_repos: Iterable[str] = (),
        store: ManagedRepoStore | None = None,
        on_allow: Callable[[str], object] | None = None,
        on_disallow: Callable[[str], object] | None = None,
        git_binary: str = GIT,
        git_timeout: float = 120.0,
    ) -> None:
        self.runner = runner
        self.static_repos = tuple(
            repo.strip().removesuffix(".git")
            for repo in static_repos
            if repo and repo.strip()
        )
        self.store = store
        self.on_allow = on_allow
        self.on_disallow = on_disallow
        self.git_binary = git_binary
        self.git_timeout = git_timeout
        self._login: str | None = None

    # ------------------------------------------------------------------ account

    async def login(self) -> str:
        if self._login is None:
            out = await self.runner.run(["api", "user", "--jq", ".login"])
            login = out.strip().splitlines()[-1] if out.strip() else ""
            if not login:
                raise GitHubAdminError("تعذر تحديد حساب GitHub المسجّل")
            self._login = login
        return self._login

    async def managed_repos(self) -> tuple[str, ...]:
        extra = await self.store.list() if self.store is not None else ()
        merged = {repo.casefold(): repo for repo in self.static_repos}
        for repo in extra:
            merged.setdefault(repo.casefold(), repo)
        return tuple(sorted(merged.values(), key=str.casefold))

    def _static_match(self, slug: str) -> bool:
        return any(
            fnmatch.fnmatchcase(slug.casefold(), pattern.casefold())
            for pattern in self.static_repos
        )

    async def is_managed(self, slug: str) -> bool:
        wanted = _slug(slug).casefold()
        return any(repo.casefold() == wanted for repo in await self.managed_repos())

    async def require_managed(self, slug: str) -> str:
        canonical = _slug(slug)
        if not await self.is_managed(canonical):
            raise GitHubAdminError(
                "هذا المستودع غير مُدار. أضفه أولًا بالأمر: /gh add owner/repo"
            )
        return canonical

    async def add_repo(self, slug: str) -> str:
        requested = _slug(slug)
        payload = self._json(
            await self.runner.run(
                ["repo", "view", requested, "--json", "nameWithOwner"]
            )
        )
        canonical = str(payload.get("nameWithOwner") or requested)
        if not self._static_match(canonical):
            if self.store is not None:
                await self.store.add(canonical)
            if self.on_allow is not None:
                self.on_allow(canonical)
        return canonical

    async def remove_repo(self, slug: str) -> bool:
        canonical = _slug(slug)
        if self._static_match(canonical):
            raise GitHubAdminError(
                "هذا المستودع مثبت في GITHUB_ALLOWED_REPOS ولا يُزال من المحادثة"
            )
        removed = await self.store.remove(canonical) if self.store is not None else False
        if self.on_disallow is not None:
            self.on_disallow(canonical)
        return removed

    # -------------------------------------------------------------------- repos

    async def repo(self, slug: str) -> RepoSummary:
        data = self._json(
            await self.runner.run(
                [
                    "repo",
                    "view",
                    _slug(slug),
                    "--json",
                    "nameWithOwner,description,visibility,defaultBranchRef,url,pushedAt",
                ]
            )
        )
        branch = data.get("defaultBranchRef")
        return RepoSummary(
            slug=str(data.get("nameWithOwner") or slug),
            description=str(data.get("description") or ""),
            visibility=str(data.get("visibility") or "PRIVATE"),
            default_branch=str(branch.get("name") if isinstance(branch, dict) else branch or ""),
            url=str(data.get("url") or ""),
            pushed_at=str(data.get("pushedAt") or ""),
        )

    async def repos(self, limit: int = 20) -> tuple[RepoSummary, ...]:
        rows = self._json(
            await self.runner.run(
                [
                    "repo",
                    "list",
                    "--limit",
                    str(max(1, min(limit, 50))),
                    "--json",
                    "nameWithOwner,description,visibility,defaultBranchRef,url,pushedAt",
                ]
            )
        )
        result = []
        for data in rows if isinstance(rows, list) else []:
            branch = data.get("defaultBranchRef")
            result.append(
                RepoSummary(
                    slug=str(data.get("nameWithOwner") or ""),
                    description=str(data.get("description") or ""),
                    visibility=str(data.get("visibility") or ""),
                    default_branch=str(
                        branch.get("name") if isinstance(branch, dict) else branch or ""
                    ),
                    url=str(data.get("url") or ""),
                    pushed_at=str(data.get("pushedAt") or ""),
                )
            )
        return tuple(result)

    async def create_repo(
        self, name: str, *, private: bool = True, description: str = ""
    ) -> RepoSummary:
        repo_name = (name or "").strip()
        if not _REPO_NAME_RE.fullmatch(repo_name):
            raise GitHubAdminError("اسم المستودع غير صالح (أحرف لاتينية وأرقام و . _ - فقط)")
        args = ["repo", "create", repo_name, "--private" if private else "--public"]
        if description.strip():
            args += ["--description", description.strip()[:350]]
        await self.runner.run(args)
        login = await self.login()
        slug = f"{login}/{repo_name}"
        await self.add_repo(slug)
        return await self.repo(slug)

    # ---------------------------------------------------------------- pull reqs

    async def pulls(
        self, slug: str, *, state: str = "open", limit: int = 10
    ) -> tuple[PullSummary, ...]:
        wanted = self._state(state)
        rows = self._json(
            await self.runner.run(
                [
                    "pr",
                    "list",
                    "--repo",
                    _slug(slug),
                    "--state",
                    wanted,
                    "--limit",
                    str(max(1, min(limit, 50))),
                    "--json",
                    "number,title,state,headRefName,baseRefName,isDraft,url,author,updatedAt",
                ]
            )
        )
        return tuple(
            PullSummary(
                number=int(data.get("number") or 0),
                title=str(data.get("title") or ""),
                state=str(data.get("state") or ""),
                head=str(data.get("headRefName") or ""),
                base=str(data.get("baseRefName") or ""),
                author=_author(data.get("author")),
                url=str(data.get("url") or ""),
                draft=bool(data.get("isDraft")),
                updated_at=str(data.get("updatedAt") or ""),
            )
            for data in (rows if isinstance(rows, list) else [])
        )

    async def pull(self, slug: str, number: int) -> PullDetail:
        data = self._json(
            await self.runner.run(
                [
                    "pr",
                    "view",
                    str(int(number)),
                    "--repo",
                    _slug(slug),
                    "--json",
                    "number,title,state,body,headRefName,baseRefName,isDraft,url,author,"
                    "updatedAt,mergeable,additions,deletions,changedFiles",
                ]
            )
        )
        summary = PullSummary(
            number=int(data.get("number") or number),
            title=str(data.get("title") or ""),
            state=str(data.get("state") or ""),
            head=str(data.get("headRefName") or ""),
            base=str(data.get("baseRefName") or ""),
            author=_author(data.get("author")),
            url=str(data.get("url") or ""),
            draft=bool(data.get("isDraft")),
            updated_at=str(data.get("updatedAt") or ""),
        )
        return PullDetail(
            summary=summary,
            body=str(data.get("body") or ""),
            mergeable=str(data.get("mergeable") or ""),
            additions=int(data.get("additions") or 0),
            deletions=int(data.get("deletions") or 0),
            changed_files=int(data.get("changedFiles") or 0),
        )

    async def create_pull(
        self,
        slug: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> str:
        canonical = await self.require_managed(slug)
        if not title.strip() or not head.strip() or not base.strip():
            raise GitHubAdminError("العنوان والفرع المصدر والفرع الهدف مطلوبة")
        args = [
            "pr",
            "create",
            "--repo",
            canonical,
            "--title",
            title.strip()[:250],
            "--head",
            head.strip(),
            "--base",
            base.strip(),
            "--body",
            body.strip(),
        ]
        if draft:
            args.append("--draft")
        out = await self.runner.run(args)
        return out.strip().splitlines()[-1] if out.strip() else ""

    async def merge_pull(self, slug: str, number: int, *, method: str = "squash") -> str:
        canonical = await self.require_managed(slug)
        chosen = (method or "squash").strip().lower()
        if chosen not in MERGE_METHODS:
            raise GitHubAdminError("طريقة الدمج يجب أن تكون squash أو merge أو rebase")
        out = await self.runner.run(
            ["pr", "merge", str(int(number)), "--repo", canonical, f"--{chosen}"]
        )
        return out.strip()

    # ------------------------------------------------------------------- issues

    async def issues(
        self, slug: str, *, state: str = "open", limit: int = 10
    ) -> tuple[IssueSummary, ...]:
        wanted = self._state(state)
        rows = self._json(
            await self.runner.run(
                [
                    "issue",
                    "list",
                    "--repo",
                    _slug(slug),
                    "--state",
                    wanted,
                    "--limit",
                    str(max(1, min(limit, 50))),
                    "--json",
                    "number,title,state,author,labels,url,updatedAt",
                ]
            )
        )
        return tuple(
            IssueSummary(
                number=int(data.get("number") or 0),
                title=str(data.get("title") or ""),
                state=str(data.get("state") or ""),
                author=_author(data.get("author")),
                labels=_labels(data.get("labels")),
                url=str(data.get("url") or ""),
                updated_at=str(data.get("updatedAt") or ""),
            )
            for data in (rows if isinstance(rows, list) else [])
        )

    async def create_issue(self, slug: str, *, title: str, body: str = "") -> str:
        canonical = await self.require_managed(slug)
        if not title.strip():
            raise GitHubAdminError("عنوان الـ issue مطلوب")
        out = await self.runner.run(
            [
                "issue",
                "create",
                "--repo",
                canonical,
                "--title",
                title.strip()[:250],
                "--body",
                body.strip(),
            ]
        )
        return out.strip().splitlines()[-1] if out.strip() else ""

    async def comment(self, slug: str, number: int, body: str) -> str:
        canonical = await self.require_managed(slug)
        if not body.strip():
            raise GitHubAdminError("نص التعليق مطلوب")
        out = await self.runner.run(
            ["issue", "comment", str(int(number)), "--repo", canonical, "--body", body.strip()]
        )
        return out.strip()

    # ---------------------------------------------------------- branches/releases

    async def branches(self, slug: str, *, limit: int = 20) -> tuple[str, ...]:
        out = await self.runner.run(
            [
                "api",
                f"repos/{_slug(slug)}/branches?per_page={max(1, min(limit, 100))}",
                "--jq",
                ".[].name",
            ]
        )
        return tuple(line.strip() for line in out.splitlines() if line.strip())

    async def releases(self, slug: str, *, limit: int = 10) -> tuple[ReleaseSummary, ...]:
        rows = self._json(
            await self.runner.run(
                [
                    "release",
                    "list",
                    "--repo",
                    _slug(slug),
                    "--limit",
                    str(max(1, min(limit, 30))),
                    "--json",
                    "tagName,name,isDraft,isPrerelease,publishedAt,url",
                ]
            )
        )
        return tuple(
            ReleaseSummary(
                tag=str(data.get("tagName") or ""),
                name=str(data.get("name") or ""),
                draft=bool(data.get("isDraft")),
                prerelease=bool(data.get("isPrerelease")),
                published_at=str(data.get("publishedAt") or ""),
                url=str(data.get("url") or ""),
            )
            for data in (rows if isinstance(rows, list) else [])
        )

    async def create_release(
        self,
        slug: str,
        *,
        tag: str,
        title: str = "",
        notes: str = "",
        draft: bool = False,
    ) -> str:
        canonical = await self.require_managed(slug)
        clean_tag = tag.strip()
        if not clean_tag:
            raise GitHubAdminError("اسم الوسم مطلوب")
        args = [
            "release",
            "create",
            clean_tag,
            "--repo",
            canonical,
            "--title",
            (title.strip() or clean_tag)[:250],
            "--notes",
            notes.strip(),
        ]
        if draft:
            args.append("--draft")
        out = await self.runner.run(args)
        return out.strip().splitlines()[-1] if out.strip() else ""

    # -------------------------------------------------------------------- push

    async def push(self, workspace: Path, message: str) -> PushResult:
        """Commit every change in the workspace and push it, without building."""
        root = Path(workspace)
        if not (root / ".git").is_dir():
            raise GitHubAdminError("المسار النشط ليس مستودع Git")
        note = (message or "").strip()
        if not note:
            raise GitHubAdminError("رسالة الالتزام مطلوبة: /gh push وصف التغيير")
        if len(note) > 500:
            raise GitHubAdminError("رسالة الالتزام طويلة جدًا (الحد 500 حرف)")
        branch = (await self._git(root, "branch", "--show-current")).strip()
        if not branch:
            raise GitHubAdminError("المستودع على detached HEAD؛ انتقل إلى فرع قبل الدفع")
        dirty = await self._git(root, "status", "--porcelain=v1")
        head = (await self._git(root, "rev-parse", "HEAD")).strip()
        if not dirty.strip():
            return PushResult(branch=branch, commit=head, changed=False,
                              message="لا توجد تغييرات للالتزام أو الدفع")
        await self._git(root, "add", "-A")
        staged = await self._git(root, "diff", "--cached", "--name-status")
        self._guard_secrets(staged)
        diff = await self._git(root, "diff", "--cached", "--unified=0")
        self._guard_secret_content(diff[:2_000_000])
        await self._git(root, "commit", "-m", note)
        commit = (await self._git(root, "rev-parse", "HEAD")).strip()
        await self._git(root, "push", "origin", branch)
        return PushResult(branch=branch, commit=commit, changed=True, message="تم الالتزام والدفع")

    def _guard_secrets(self, staged: str) -> None:
        for line in staged.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status, path = parts[0], parts[-1]
            if status.startswith("D"):
                continue
            name = Path(path).name
            if name.casefold() in _SECRET_FILE_EXCEPTIONS:
                continue
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in _SECRET_FILES):
                raise GitHubAdminError(f"رُفض الدفع: الملف {path} قد يحتوي أسرارًا")

    @staticmethod
    def _guard_secret_content(diff: str) -> None:
        for pattern in _SECRET_CONTENT:
            if pattern.search(diff or ""):
                raise GitHubAdminError("رُفض الدفع: فرق التغييرات يحتوي مفتاحًا سريًا")

    async def _git(self, cwd: Path, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            self.git_binary,
            "-C",
            str(cwd),
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.git_timeout
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise GitHubAdminError("انتهت مهلة عملية Git قبل اكتمالها") from exc
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if process.returncode:
            detail = (err or out or "Git أعاد خطأ غير موصوف").strip().splitlines()
            raise GitHubAdminError(
                f"فشلت عملية Git: {detail[-1][:400] if detail else 'خطأ غير معروف'}"
            )
        return out

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _state(value: str) -> str:
        state = (value or "open").strip().lower()
        if state not in PR_STATES:
            raise GitHubAdminError("الحالة يجب أن تكون open أو closed أو all")
        return state

    @staticmethod
    def _json(value: str) -> Any:
        try:
            return json.loads(value or "null")
        except json.JSONDecodeError as exc:
            raise GitHubAdminError("رد GitHub لم يكن JSON صالحًا") from exc
