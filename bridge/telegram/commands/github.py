"""Thin Telegram adapter for GitHub account and repository administration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.github_admin_service import GitHubAdminError, GitHubAdminService
from bridge.services.workspace_service import WorkspaceService, WorkspaceUnavailable
from bridge.telegram.rendering.github import (
    render_branches,
    render_help,
    render_issues,
    render_pull,
    render_pulls,
    render_push,
    render_releases,
    render_repo,
    render_repos,
    render_status,
)
from workspace_manager import WorkspaceError

Reply = Callable[[Any, str], Awaitable[None]]
AuditWrite = Callable[..., None]


class GitHubCommands:
    def __init__(
        self,
        service: GitHubAdminService,
        workspace_service: WorkspaceService,
        *,
        reply: Reply,
        audit_write: AuditWrite,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.workspace_service = workspace_service
        self.reply = reply
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def _owner(update: Update) -> str:
        if update.effective_user is None:
            raise GitHubAdminError("تعذر تحديد المستخدم")
        return str(update.effective_user.id)

    @staticmethod
    def _split(value: str) -> list[str]:
        return [part.strip() for part in value.split("|")]

    async def _active(self, owner_id: str) -> Any:
        active = await self.workspace_service.active(owner_id)
        if active is None:
            raise WorkspaceUnavailable("ما في مستودع نشط. استخدم /gh use owner/repo أو /use")
        return active

    async def _active_slug(self, owner_id: str) -> str:
        return str((await self._active(owner_id)).repo_slug)

    async def _slug_or_active(self, owner_id: str, value: str) -> str:
        token = (value or "").strip()
        return token if "/" in token else await self._active_slug(owner_id)

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        text = " ".join(context.args).strip()
        if not text or text in {"help", "-h", "?", "مساعدة"}:
            await self.reply(update.message, render_help())
            return
        sub, _, rest = text.partition(" ")
        rest = rest.strip()
        try:
            await self._dispatch(update, sub.lower(), rest)
        except (GitHubAdminError, WorkspaceError, WorkspaceUnavailable) as exc:
            await self.reply(update.message, f"تعذرت عملية GitHub: {exc}.")
        except Exception as exc:
            self.log.warning("فشل أمر GitHub /gh %s: %s", sub, type(exc).__name__)
            await self.reply(update.message, f"تعذر تنفيذ أمر GitHub ({type(exc).__name__}).")

    async def _dispatch(self, update: Update, sub: str, rest: str) -> None:
        owner = self._owner(update)
        audit = self.audit_write
        if sub == "status":
            login = await self.service.login()
            managed = await self.service.managed_repos()
            active = ""
            try:
                active = await self._active_slug(owner)
            except WorkspaceUnavailable:
                active = ""
            await self.reply(update.message, render_status(login, active, managed))
            return
        if sub == "managed":
            managed = await self.service.managed_repos()
            await self.reply(
                update.message,
                "المستودعات المُدارة:\n" + ("\n".join(f"- {slug}" for slug in managed) or "- لا يوجد"),
            )
            return
        if sub == "repos":
            limit = 20
            if rest.isdigit():
                limit = int(rest)
            items = await self.service.repos(limit)
            managed = await self.service.managed_repos()
            await self.reply(update.message, render_repos(items, managed))
            return
        if sub == "repo":
            slug = await self._slug_or_active(owner, rest)
            info = await self.service.repo(slug)
            managed = await self.service.is_managed(info.slug)
            active = ""
            try:
                active = await self._active_slug(owner)
            except WorkspaceUnavailable:
                active = ""
            await self.reply(
                update.message,
                render_repo(info, managed=managed, active=active.casefold() == info.slug.casefold()),
            )
            return
        if sub == "add":
            if not rest:
                raise GitHubAdminError("استخدم: /gh add owner/repo")
            canonical = await self.service.add_repo(rest)
            audit("github_repo_added", "accepted", actor_id=owner, details={"repo": canonical})
            await self.reply(update.message, f"تم استلام إدارة {canonical}.\nاستخدمه بالأمر: /gh use {canonical}")
            return
        if sub == "remove":
            if not rest:
                raise GitHubAdminError("استخدم: /gh remove owner/repo")
            canonical = await self.service.remove_repo(rest)
            audit("github_repo_removed", "accepted", actor_id=owner, details={"repo": rest})
            await self.reply(
                update.message,
                f"أُزيل {rest} من قائمة الإدارة." if canonical else f"{rest} لم يكن في قائمة الإدارة.",
            )
            return
        if sub == "create":
            if not rest:
                raise GitHubAdminError("استخدم: /gh create name [private|public] [| وصف]")
            parts = self._split(rest)
            head = parts[0]
            tokens = head.split()
            name = tokens[0]
            private = True
            for token in tokens[1:]:
                if token.lower() in {"public", "عام"}:
                    private = False
                elif token.lower() in {"private", "خاص"}:
                    private = True
            description = parts[1] if len(parts) > 1 else ""
            info = await self.service.create_repo(name, private=private, description=description)
            audit("github_repo_created", "accepted", actor_id=owner, details={"repo": info.slug})
            await self.reply(
                update.message,
                f"أُنشئ المستودع وأُضيف للإدارة:\n{info.slug}\n{info.url}",
            )
            return
        if sub == "use":
            if not rest:
                raise GitHubAdminError("استخدم: /gh use owner/repo")
            status = await self.workspace_service.select(owner, rest)
            audit("workspace_selected", "accepted", actor_id=owner, details={"repo": status.slug})
            await self.reply(
                update.message,
                f"المستودع النشط: {status.slug}\nالفرع: {status.branch}",
            )
            return
        if sub == "sync":
            status = await self.workspace_service.sync(owner)
            await self.reply(update.message, f"تمت مزامنة {status.slug} — {status.branch}")
            return
        if sub == "push":
            if not rest:
                raise GitHubAdminError("استخدم: /gh push وصف التغيير")
            active = await self._active(owner)
            from pathlib import Path as _Path

            result = await self.service.push(_Path(str(active.directory)), rest)
            audit(
                "github_push",
                "accepted",
                actor_id=owner,
                details={"repo": active.repo_slug, "changed": result.changed},
            )
            await self.reply(update.message, render_push(active.repo_slug, result))
            return
        if sub == "prs":
            tokens = rest.split()
            slug = ""
            state = "open"
            for token in tokens:
                if "/" in token:
                    slug = token
                elif token.lower() in {"open", "closed", "all"}:
                    state = token.lower()
            slug = slug or await self._active_slug(owner)
            items = await self.service.pulls(slug, state=state)
            await self.reply(update.message, render_pulls(slug, items))
            return
        if sub == "pr":
            tokens = rest.split()
            number = None
            slug = ""
            for token in tokens:
                if "/" in token:
                    slug = token
                elif token.isdigit():
                    number = int(token)
            if number is None:
                raise GitHubAdminError("استخدم: /gh pr [owner/repo] رقم")
            slug = slug or await self._active_slug(owner)
            detail = await self.service.pull(slug, number)
            await self.reply(update.message, render_pull(detail))
            return
        if sub == "prnew":
            parts = self._split(rest)
            if len(parts) < 3:
                raise GitHubAdminError("استخدم: /gh prnew العنوان | الفرع | الهدف [| الوصف]")
            slug = await self._active_slug(owner)
            url = await self.service.create_pull(
                slug,
                title=parts[0],
                head=parts[1],
                base=parts[2],
                body=parts[3] if len(parts) > 3 else "",
            )
            audit("github_pull_created", "accepted", actor_id=owner, details={"repo": slug})
            await self.reply(update.message, f"أُنشئ طلب الدمج:\n{url}")
            return
        if sub == "merge":
            tokens = rest.split()
            if not tokens or not tokens[0].isdigit():
                raise GitHubAdminError("استخدم: /gh merge رقم [squash|merge|rebase]")
            method = tokens[1].lower() if len(tokens) > 1 else "squash"
            slug = await self._active_slug(owner)
            out = await self.service.merge_pull(slug, int(tokens[0]), method=method)
            audit("github_pull_merged", "accepted", actor_id=owner, details={"repo": slug})
            await self.reply(update.message, f"تم الدمج:\n{out[:3500]}")
            return
        if sub == "issues":
            tokens = rest.split()
            slug = ""
            state = "open"
            for token in tokens:
                if "/" in token:
                    slug = token
                elif token.lower() in {"open", "closed", "all"}:
                    state = token.lower()
            slug = slug or await self._active_slug(owner)
            items = await self.service.issues(slug, state=state)
            await self.reply(update.message, render_issues(slug, items))
            return
        if sub == "issue":
            parts = self._split(rest)
            if not parts or not parts[0]:
                raise GitHubAdminError("استخدم: /gh issue العنوان | الوصف")
            slug = await self._active_slug(owner)
            url = await self.service.create_issue(
                slug, title=parts[0], body=parts[1] if len(parts) > 1 else ""
            )
            audit("github_issue_created", "accepted", actor_id=owner, details={"repo": slug})
            await self.reply(update.message, f"أُنشئت المشكلة:\n{url}")
            return
        if sub == "comment":
            tokens = rest.split(maxsplit=1)
            if len(tokens) < 2 or not tokens[0].isdigit():
                raise GitHubAdminError("استخدم: /gh comment رقم النص")
            slug = await self._active_slug(owner)
            out = await self.service.comment(slug, int(tokens[0]), tokens[1])
            await self.reply(update.message, f"تم التعليق:\n{out[:3500]}")
            return
        if sub == "branches":
            slug = await self._slug_or_active(owner, rest)
            items = await self.service.branches(slug)
            await self.reply(update.message, render_branches(slug, items))
            return
        if sub == "releases":
            slug = await self._slug_or_active(owner, rest)
            items = await self.service.releases(slug)
            await self.reply(update.message, render_releases(slug, items))
            return
        if sub == "relnew":
            parts = self._split(rest)
            if not parts or not parts[0]:
                raise GitHubAdminError("استخدم: /gh relnew tag | العنوان [| الملاحظات]")
            slug = await self._active_slug(owner)
            url = await self.service.create_release(
                slug,
                tag=parts[0],
                title=parts[1] if len(parts) > 1 else "",
                notes=parts[2] if len(parts) > 2 else "",
            )
            audit("github_release_created", "accepted", actor_id=owner, details={"repo": slug})
            await self.reply(update.message, f"أُنشئ الإصدار:\n{url}")
            return
        await self.reply(update.message, render_help())
