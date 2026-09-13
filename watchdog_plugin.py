"""Background watchdog integration and Telegram watchdog command."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from telegram.ext import CommandHandler

import watchdog_runner

BRIDGE_DIR = Path(__file__).resolve().parent
REPORT_PATH = BRIDGE_DIR / "runtime" / "watchdog-latest.json"
INTERVAL_SECONDS = max(60, int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "300")))
_task: asyncio.Task[None] | None = None
_stopped = asyncio.Event()


def _read_report() -> dict[str, Any] | None:
    try:
        value = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _render_report(report: dict[str, Any]) -> str:
    status = str(report.get("status", "unknown")).upper()
    score = report.get("score", "?")
    resources = report.get("resources") if isinstance(report.get("resources"), dict) else {}
    services = report.get("services") if isinstance(report.get("services"), dict) else {}
    queue = report.get("queue") if isinstance(report.get("queue"), dict) else {}
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    actions = report.get("actions") if isinstance(report.get("actions"), list) else []

    lines = [
        f"🛡 Watchdog: {status} — {score}/100",
        f"OpenCode: {'✅' if services.get('opencode_http_healthy') else '❌'}",
        f"Telegram bridge: {'✅' if services.get('telegram_bridge_active') else '❌'}",
        f"Queue: {queue.get('queued', 0)} queued / {queue.get('running', 0)} running",
        f"Memory: {resources.get('available_memory_mib', '?')} MiB available",
        f"Disk free: {resources.get('disk_free_percent', '?')}%",
    ]
    if issues:
        lines.append("Issues:")
        lines.extend(f"• {str(issue)}" for issue in issues[:6])
    if actions:
        lines.append("Recovery:")
        for action in actions[:4]:
            if isinstance(action, dict):
                lines.append(
                    f"• {action.get('service', '?')}: {action.get('outcome', '?')} — {action.get('reason', '')}"
                )
    if report.get("manual_action_required"):
        lines.append("⚠️ Manual review required.")
    return "\n".join(lines)


async def _watchdog_command(update, context) -> None:
    if not update.message:
        return
    report = _read_report()
    if report is None:
        await update.message.reply_text("Watchdog has not produced a health report yet.")
        return
    await update.message.reply_text(_render_report(report), disable_web_page_preview=True)


async def _run_loop() -> None:
    while not _stopped.is_set():
        try:
            await asyncio.to_thread(watchdog_runner.main)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        try:
            await asyncio.wait_for(_stopped.wait(), timeout=INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def install(app) -> None:
    global _task
    app.add_handler(CommandHandler("watchdog", _watchdog_command))
    if _task is None:
        _stopped.clear()
        _task = asyncio.create_task(_run_loop(), name="opencode-bridge-watchdog")


async def close() -> None:
    global _task
    _stopped.set()
    if _task is None:
        return
    _task.cancel()
    await asyncio.gather(_task, return_exceptions=True)
    _task = None
