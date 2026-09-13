"""Telegram commands for GitHub Actions visibility on the active V3 workspace."""

from __future__ import annotations

import os

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

import bot as core
from github_ci import GitHubCIClient, GitHubCIError
from workspace_manager import WorkspaceError
import v3_plugin

CI_WORKFLOW = os.environ.get("GITHUB_CI_WORKFLOW", "CI").strip() or None
client = GitHubCIClient()


def render_ci(status) -> str:
    icons = {
        "success": "✅",
        "failure": "❌",
        "pending": "⏳",
        "not_found": "⚪",
        "timeout": "⌛",
        "unknown": "❔",
    }
    lines = [f"{icons.get(status.state, 'ℹ️')} CI: {status.state}"]
    if status.workflow:
        lines.append(f"Workflow: {status.workflow}")
    if status.branch:
        lines.append(f"Branch: {status.branch}")
    if status.head_sha:
        lines.append(f"Commit: {status.head_sha[:12]}")
    if status.conclusion:
        lines.append(f"Conclusion: {status.conclusion}")
    if status.failed_steps:
        lines.append("Failed steps:")
        lines.extend(f"• {item}" for item in status.failed_steps[:8])
    if status.html_url:
        lines.append(status.html_url)
    return "\n".join(lines)


@core.authorized
async def cmd_ci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    try:
        active = await v3_plugin._active(str(update.effective_user.id))
        if active is None:
            raise WorkspaceError("اختَر مشروع أولًا باستخدام /use owner/repo")
        repo = await v3_plugin.workspace_manager.status(active.repo_slug)
        status = await client.latest_run(active.repo_slug, branch=repo.branch, workflow=CI_WORKFLOW)
        await core._safe_reply(update.message, render_ci(status))
        core.audit.write(
            "workspace_ci_checked",
            "accepted",
            actor_id=update.effective_user.id,
            details={"repo": active.repo_slug, "state": status.state, "run_id": status.run_id},
        )
    except (WorkspaceError, GitHubCIError) as exc:
        await core._safe_reply(update.message, f"تعذر فحص CI: {exc}.")
    except Exception as exc:
        core.log.warning("تعذر فحص GitHub Actions: %s", type(exc).__name__)
        await core._safe_reply(update.message, "تعذر فحص GitHub Actions حاليًا.")


async def install(app: Application) -> None:
    app.add_handler(CommandHandler("ci", cmd_ci), group=-2)
    existing = await app.bot.get_my_commands()
    command = BotCommand("ci", "Show active repository CI status")
    await app.bot.set_my_commands([command] + [item for item in existing if item.command != "ci"])


async def close() -> None:
    await client.close()
