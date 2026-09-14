"""Telegram diagnostics for host pressure and adaptive worker sizing."""

from __future__ import annotations

import os

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

import bot as core
from adaptive_workers import WorkerLimitStatus
from resource_monitor import HostResourcePolicy, format_decision
from shadow_policy_audit import ShadowReadiness, format_readiness

_policy = HostResourcePolicy(cache_seconds=3.0)


def _configured_workers() -> int:
    try:
        value = int(os.environ.get("AGENT_TASK_WORKERS", "2"))
    except ValueError:
        value = 2
    return max(1, min(value, 8))


def _controller_status() -> WorkerLimitStatus | None:
    service = getattr(core, "task_service", None)
    limiter = getattr(service, "worker_limit", None)
    status = getattr(limiter, "status", None)
    if not callable(status):
        return None
    try:
        return status()
    except Exception:
        return None


def _shadow_readiness() -> ShadowReadiness | None:
    service = getattr(core, "task_service", None)
    policy = getattr(service, "resource_policy", None)
    readiness = getattr(policy, "readiness", None)
    if not callable(readiness):
        return None
    try:
        return readiness()
    except Exception:
        return None


def _format_controller(status: WorkerLimitStatus) -> str:
    recovery = (
        "ready"
        if status.recovery_remaining_seconds <= 0
        else f"{status.recovery_remaining_seconds:.1f}s remaining"
    )
    return (
        "\n\nAdaptive controller\n"
        f"Stable workers: {status.stable_workers}/{status.configured_workers}\n"
        f"Raw target: {status.raw_target_workers}\n"
        f"Controller pressure: {status.pressure}\n"
        f"Controller health: {status.health_score}/100\n"
        f"Recovery: {recovery}\n"
        f"Last policy reason: {status.reason}"
    )


@core.authorized
async def cmd_resources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    configured = _configured_workers()
    decision = _policy.decide(configured)
    text = "Host resources\n\n" + format_decision(decision)
    status = _controller_status()
    if status is not None:
        text += _format_controller(status)
    readiness = _shadow_readiness()
    if readiness is not None:
        text += "\n\n" + format_readiness(readiness)
    await core._safe_reply(update.message, text)


async def install(app: Application) -> None:
    app.add_handler(CommandHandler("resources", cmd_resources), group=-2)
    existing = await app.bot.get_my_commands()
    if not any(command.command == "resources" for command in existing):
        await app.bot.set_my_commands([BotCommand("resources", "Show host resource pressure")] + list(existing))
