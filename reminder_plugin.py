"""Wire owner reminders into the production Telegram application."""

from __future__ import annotations

import logging

import bot as core
from bridge.services.reminder_service import ReminderScheduler, ReminderStore
from bridge.telegram.commands.reminders import ReminderCommands

log = logging.getLogger(__name__)

_store: ReminderStore | None = None
_scheduler: ReminderScheduler | None = None
_commands: ReminderCommands | None = None


async def install(app) -> None:
    """Start the scheduler once, after the core task store is available."""
    global _store, _scheduler, _commands
    if _store is not None and _scheduler is not None and _commands is not None:
        await _scheduler.start()
        return
    store = ReminderStore(core.task_store.database)
    try:
        reset = await store.reset_interrupted()
        if reset:
            log.warning("recovered %d interrupted reminder deliveries", reset)
    except Exception:
        log.exception("failed to recover interrupted reminder deliveries")

    async def send(chat_id: int, text: str) -> None:
        await app.bot.send_message(chat_id=chat_id, text=text)

    scheduler = ReminderScheduler(store, send, logger=log)
    commands = ReminderCommands(
        store,
        scheduler,
        reply=lambda update, text: core._safe_reply(update.effective_message, text),
    )
    _store, _scheduler, _commands = store, scheduler, commands
    await scheduler.start()


def commands() -> ReminderCommands:
    if _commands is None:
        raise RuntimeError("reminder service is not installed")
    return _commands


async def close() -> None:
    scheduler = _scheduler
    if scheduler is not None:
        await scheduler.stop()
