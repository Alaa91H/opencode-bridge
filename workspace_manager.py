"""Safe allow-listed Git workspace management for the Telegram development agent."""

from __future__ import annotations

import asyncio
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepoStatus:
    slug: str
    directory: Path
    branch: str
    dirty: bool
    summary: str


class GitWorkspaceManager:
    """Manage only repositories explicitly allowed by configuration."""

    def __init__(self, root: Path, allowed_repos: Iterable[str], git_timeout: float = 120.0) -> None:
        self.root = root.expanduser().resolve()
        self.allowed_repos = tuple(x.strip().removesuffix(".git") for x in allowed_repos if x.strip())
        self.git_timeout = git_timeout
        self._locks: dict[str, asyncio.Lock] = {}

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_slug(value: str) -> str:
        raw = value.strip()
        if raw.startswith("git@github.com:"):
            raw = raw.split(":", 1)[1]
        elif raw.startswith(("https://", "http://", "ssh://")):
            parsed = urlparse(raw)
            if parsed.hostname and parsed.hostname.casefold() != "github.com":
                raise WorkspaceError("المستودعات البعيدة مسموحة من GitHub فقط")
            raw = parsed.path.lstrip("/")
        raw = raw.removesuffix(".git").strip("/")
        if not _SLUG_RE.fullmatch(raw):
            raise WorkspaceError("استخدم اسم مستودع بصيغة owner/repo")
        return raw

    def require_allowed(self, value: str) -> str:
        slug = self.normalize_slug(value)
        if not self.allowed_repos:
            raise WorkspaceError("لم تُضبط GITHUB_ALLOWED_REPOS على الخادم")
        allowed = any(fnmatch.fnmatchcase(slug.casefold(), p.casefold()) for p in self.allowed_repos)
        if not allowed:
            raise WorkspaceError(f"المستودع {slug} غير موجود ضمن GITHUB_ALLOWED_REPOS")
        return slug

    def repo_path(self, value: str) -> Path:
        slug = self.require_allowed(value)
        owner, repo = slug.split("/", 1)
        path = (self.root / owner / repo).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("مسار المستودع خارج جذر مساحات العمل") from exc
        return path

    def configured_repos(self) -> tuple[str, ...]:
        return self.allowed_repos

    def local_repos(self) -> list[str]:
        repos: list[str] = []
        for pattern in self.allowed_repos:
            if any(c in pattern for c in "*?["):
                continue
            path = self.repo_path(pattern)
            if (path / ".git").is_dir():
                repos.append(self.normalize_slug(pattern))
        return sorted(set(repos), key=str.casefold)

    def _lock(self, slug: str) -> asyncio.Lock:
        return self._locks.setdefault(slug.casefold(), asyncio.Lock())

    async def _run_git(self, args: list[str], cwd: Path | None = None, check: bool = True) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.git_timeout)
        except asyncio.TimeoutError as exc:
            raise WorkspaceError("انتهت مهلة عملية Git قبل اكتمالها") from exc
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if check and process.returncode:
            detail = (err or out or "Git أعاد خطأ غير موصوف").splitlines()[-1]
            raise WorkspaceError(f"فشلت عملية Git: {detail[:400]}")
        return int(process.returncode or 0), out, err

    async def _git(self, path: Path, *args: str, check: bool = True) -> tuple[int, str, str]:
        return await self._run_git(["-C", str(path), *args], check=check)

    async def ensure_repo(self, value: str, sync: bool = True) -> RepoStatus:
        slug = self.require_allowed(value)
        path = self.repo_path(slug)
        self.ensure_root()
        async with self._lock(slug):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                await self._run_git(["clone", "--no-tags", f"https://github.com/{slug}.git", str(path)])
            elif not (path / ".git").is_dir():
                raise WorkspaceError("المسار موجود لكنه ليس مستودع Git")
            await self._verify_origin(path, slug)
            if sync:
                await self._sync(path)
            return await self._status(slug, path)

    async def sync_repo(self, value: str) -> RepoStatus:
        slug = self.require_allowed(value)
        path = self.repo_path(slug)
        if not (path / ".git").is_dir():
            return await self.ensure_repo(slug, sync=True)
        async with self._lock(slug):
            await self._verify_origin(path, slug)
            await self._sync(path)
            return await self._status(slug, path)

    async def status(self, value: str) -> RepoStatus:
        slug = self.require_allowed(value)
        path = self.repo_path(slug)
        if not (path / ".git").is_dir():
            raise WorkspaceError("المستودع غير مستنسخ على الخادم بعد")
        async with self._lock(slug):
            return await self._status(slug, path)

    async def _verify_origin(self, path: Path, slug: str) -> None:
        _, origin, _ = await self._git(path, "remote", "get-url", "origin")
        if self.normalize_slug(origin).casefold() != slug.casefold():
            raise WorkspaceError("remote origin لا يطابق المستودع المسموح")

    async def _default_branch(self, path: Path) -> str:
        code, out, _ = await self._git(path, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
        if code == 0 and out.startswith("origin/"):
            return out.split("/", 1)[1]
        for branch in ("main", "master"):
            code, _, _ = await self._git(path, "show-ref", "--verify", f"refs/remotes/origin/{branch}", check=False)
            if code == 0:
                return branch
        raise WorkspaceError("تعذر تحديد الفرع الافتراضي")

    async def _sync(self, path: Path) -> None:
        await self._git(path, "fetch", "--prune", "origin")
        _, dirty, _ = await self._git(path, "status", "--porcelain=v1")
        if dirty:
            return
        branch = await self._default_branch(path)
        await self._git(path, "switch", branch)
        await self._git(path, "merge", "--ff-only", f"origin/{branch}")

    async def _status(self, slug: str, path: Path) -> RepoStatus:
        _, branch, _ = await self._git(path, "branch", "--show-current")
        _, dirty, _ = await self._git(path, "status", "--porcelain=v1")
        _, summary, _ = await self._git(path, "status", "--short", "--branch")
        return RepoStatus(slug, path, branch or "detached", bool(dirty), summary.splitlines()[0] if summary else "")
