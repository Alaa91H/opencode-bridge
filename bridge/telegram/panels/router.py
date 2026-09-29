"""Panel execution context and the callback router behind /menu."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from bridge.telegram.panels.registry import (
    ACT,
    CONFIRM_NO,
    CONFIRM_YES,
    MENU,
    NAVIGATE,
    Panel,
    PanelAction,
    PanelError,
    PanelView,
    RAW,
    action_callback,
    assert_short,
    cancel_callback,
    confirm_callback,
    navigate_callback,
    parse,
)

log = logging.getLogger("opencode_bridge.panels")

Provider = Callable[[], Awaitable[Any]]
ActionHandler = Callable[["PanelContext", str], Awaitable[None]]


class PanelContext:
    """Owner-scoped access to the data a screen declares it needs.

    Screens never reach for services directly; they ask the context for a named
    value, which keeps every screen testable with plain callables and keeps the
    ``bridge/services`` layer free of Telegram imports.
    """

    def __init__(self, owner_id: str, providers: dict[str, Provider]) -> None:
        self.owner_id = owner_id
        self._providers = providers

    async def fetch(self, keys: tuple[str, ...]) -> dict[str, Any]:
        missing = [key for key in keys if key not in self._providers]
        if missing:
            raise PanelError(f"no provider for: {', '.join(sorted(missing))}")
        if not keys:
            return {}
        results = await _gather_all(self._providers, keys)
        return dict(zip(keys, results))

    async def one(self, key: str) -> Any:
        return (await self.fetch((key,)))[key]


async def _gather_all(providers: dict[str, Provider], keys: tuple[str, ...]) -> list[Any]:
    """Await every provider, tolerating a provider that is a plain callable."""
    import asyncio
    import inspect

    results: list[Any] = []
    for key in keys:
        value = providers[key]()
        results.append(await value if inspect.isawaitable(value) else value)
    return results


class PanelRouter:
    """Render panels, run their actions, and keep navigation reversible.

    ``CallbackQueryHandler`` bypasses the ``@authorized`` decorator, so
    authorization is re-checked on every single press here.
    """

    def __init__(
        self,
        *,
        panels: dict[str, Panel],
        providers: Callable[[str], PanelContext],
        actions: dict[tuple[str, str], ActionHandler] | None = None,
        is_allowed: Callable[[Update], bool],
        logger: logging.Logger | None = None,
    ) -> None:
        self.panels = panels
        self.providers = providers
        self.actions = actions or {}
        self.is_allowed = is_allowed
        self.log = logger or logging.getLogger(__name__)

    def panel(self, name: str) -> Panel:
        panel = self.panels.get(name)
        if panel is None:
            raise PanelError(f"unknown screen: {name}")
        return panel

    async def render(self, name: str, owner_id: str) -> tuple[str, InlineKeyboardMarkup]:
        panel = self.panel(name)
        context = self.providers(owner_id)
        data = await context.fetch(panel.needs)
        view = await panel.render(context, data)
        if not isinstance(view, PanelView):
            view = PanelView(text=str(view), rows=())
        return view.text, self.keyboard(view, panel.name, name)

    def keyboard(self, view: PanelView, screen: str, name: str) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for row in view.rows:
            buttons: list[InlineKeyboardButton] = []
            for action in row:
                if action.verb == RAW:
                    data = action.arg
                elif action.verb == NAVIGATE:
                    data = navigate_callback(action.arg or MENU)
                else:
                    data = action_callback(screen, action.verb, action.arg)
                assert_short(data)
                label = f"{'🗑 ' if action.destructive else ''}{action.label}"
                buttons.append(InlineKeyboardButton(label, callback_data=data))
            if buttons:
                rows.append(buttons)
        if name != MENU:
            data = navigate_callback(MENU)
            assert_short(data)
            rows.append([InlineKeyboardButton("‹ رجوع للقائمة", callback_data=data)])
        return InlineKeyboardMarkup(rows)

    async def show(self, update: Update) -> None:
        """Entry point for the ``/menu`` command handler."""
        owner_id = self._owner(update)
        if owner_id is None:
            return
        if not self.is_allowed(update):
            await self._reply(update, "غير مصرح لك باستخدام هذا الأمر.")
            return
        try:
            text, keyboard = await self.render(MENU, owner_id)
        except Exception as exc:
            self.log.exception("فشل عرض لوحة التحكم")
            await self._reply(update, user_error(exc, "عرض لوحة التحكم"))
            return
        message = update.effective_message
        if message is not None:
            await message.reply_text(text, reply_markup=keyboard, disable_web_page_preview=True)

    async def handle(self, update: Update, context: Any) -> None:
        query = update.callback_query
        if query is None or not isinstance(query.data, str):
            return
        if not query.data.startswith("pnl:"):
            return
        if not self.is_allowed(update):
            await query.answer("غير مصرح لك باستخدام هذا الزر.", show_alert=True)
            return
        owner_id = self._owner(update)
        if owner_id is None:
            return
        try:
            parts = parse(query.data)
            verb = parts[1]
            if verb == NAVIGATE:
                target = parts[2] if len(parts) > 2 else MENU
                await self._edit(update, *(await self.render(target, owner_id)))
                await query.answer()
                return
            if verb == CONFIRM_NO:
                screen = parts[2]
                await self._edit(update, *(await self.render(screen, owner_id)))
                await query.answer("تم الإلغاء")
                return
            if verb in (ACT, CONFIRM_YES):
                screen = parts[2]
                action_verb = parts[3] if len(parts) > 3 else ""
                arg = parts[4] if len(parts) > 4 else ""
                await self._run(
                    update, query, owner_id, screen, action_verb, arg, confirmed=verb == CONFIRM_YES
                )
                return
            raise PanelError(f"unsupported panel verb: {verb}")
        except PanelError as exc:
            self.log.info("تعذر تنفيذ زر اللوحة: %s", exc)
            await query.answer(str(exc), show_alert=True)
            await self._refresh(update, owner_id)
            return
        except Exception as exc:
            self.log.exception("فشل التعامل مع زر لوحة التحكم")
            await query.answer(user_error(exc, "تنفيذ الإجراء"), show_alert=True)

    async def _run(
        self,
        update: Update,
        query: Any,
        owner_id: str,
        screen: str,
        verb: str,
        arg: str,
        *,
        confirmed: bool,
    ) -> None:
        panel = self.panel(screen)
        context = self.providers(owner_id)
        data = await context.fetch(panel.needs)
        view = await panel.render(context, data)
        action = _find_action(view, verb, arg)
        if action is None:
            raise PanelError("هذا الزر لم يعد متاحًا؛ حدّث القائمة")
        if action.confirm and not confirmed:
            await self._edit(update, *_confirmation(panel.title, screen, verb, arg, action))
            await query.answer("أكّد الإجراء")
            return
        handler = self.actions.get((screen, verb))
        if handler is not None:
            await handler(context, arg)
        await self._edit(update, *(await self.render(screen, owner_id)))
        await query.answer("تم ✅")

    async def _refresh(self, update: Update, owner_id: str) -> None:
        try:
            await self._edit(update, *(await self.render(MENU, owner_id)))
        except Exception as exc:
            self.log.info("تعذر تحديث اللوحة: %s", type(exc).__name__)

    async def _edit(self, update: Update, text: str, keyboard: InlineKeyboardMarkup) -> None:
        query = update.callback_query
        if query is None or query.message is None:
            return
        await update.get_bot().edit_message_text(
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=text,
            reply_markup=keyboard,
        )

    async def _reply(self, update: Update, text: str) -> None:
        message = update.effective_message
        if message is not None:
            await message.reply_text(text, disable_web_page_preview=True)

    @staticmethod
    def _owner(update: Update) -> str | None:
        user = update.effective_user
        return str(user.id) if user is not None else None


def _find_action(view: PanelView, verb: str, arg: str) -> PanelAction | None:
    for row in view.rows:
        for action in row:
            if action.verb == verb and action.arg == arg:
                return action
    return None


def _confirmation(
    title: str, screen: str, verb: str, arg: str, action: PanelAction
) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"⚠️ {action.confirm or 'هل تريد المتابعة؟'}\n\n"
        f"الشاشة: {title}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تأكيد", callback_data=confirm_callback(screen, verb, arg)),
                InlineKeyboardButton("❌ إلغاء", callback_data=cancel_callback(screen)),
            ]
        ]
    )
    assert_short(*[button.callback_data for row in keyboard.inline_keyboard for button in row])
    return text, keyboard


def user_error(exc: Exception, operation: str) -> str:
    from messages import user_error as _user_error

    return _user_error(exc, operation)
