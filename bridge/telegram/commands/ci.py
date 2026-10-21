"""Thin Telegram adapter for GitHub CI visibility."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.workspace_service import WorkspaceUnavailable
from bridge.telegram.rendering.ci import render_ci
from github_ci import GitHubCIError
from workspace_manager import WorkspaceError

Reply = Callable[[Any, str], Awaitable[None]]
AuditWrite = Callable[..., None]


class CICommands:
    def __init__(
        self,
        service: Any,
        *,
        reply: Reply,
        audit_write: AuditWrite,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)

    async def ci(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        try:
            repo, status = await self.service.latest(str(update.effective_user.id))
            await self.reply(update.message, render_ci(status))
            self.audit_write(
                "workspace_ci_checked",
                "accepted",
                actor_id=update.effective_user.id,
                details={"repo": repo.slug, "state": status.state, "run_id": status.run_id},
            )
        except (WorkspaceError, WorkspaceUnavailable, GitHubCIError) as exc:
            await self.reply(update.message, f"تعذر فحص CI: {exc}.")
        except Exception as exc:
            self.log.warning("تعذر فحص GitHub Actions: %s", type(exc).__name__)
            await self.reply(update.message, "تعذر فحص GitHub Actions حاليًا.")
