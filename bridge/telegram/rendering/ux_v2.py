"""Telegram UX v2 view models, pagination and action keyboards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UXAction(str, Enum):
    RETRY = "retry"
    CANCEL = "cancel"
    DUPLICATE = "duplicate"
    RERUN = "rerun"
    DOWNLOAD = "download"
    CONFIRM = "confirm"
    BACK = "back"


@dataclass(frozen=True)
class UXButton:
    label: str
    callback_data: str


@dataclass(frozen=True)
class UXPage:
    title: str
    body: str
    breadcrumbs: tuple[str, ...]
    buttons: tuple[UXButton, ...]
    page: int
    pages: int


class UXRenderer:
    def page(self, *, title: str, items: list[str], page: int = 0, per_page: int = 8,
             breadcrumbs: tuple[str, ...] = (), actions: tuple[UXAction, ...] = (),
             entity_id: str = "") -> UXPage:
        if per_page <= 0:
            raise ValueError("per_page must be positive")
        total = max(1, (len(items) + per_page - 1) // per_page)
        page = min(max(page, 0), total - 1)
        visible = items[page * per_page:(page + 1) * per_page]
        buttons = [UXButton(a.value.title(), f"ux:{a.value}:{entity_id}") for a in actions]
        if page > 0:
            buttons.append(UXButton("Previous", f"ux:page:{page - 1}:{entity_id}"))
        if page + 1 < total:
            buttons.append(UXButton("Next", f"ux:page:{page + 1}:{entity_id}"))
        return UXPage(title, "\n".join(visible) or "—", breadcrumbs, tuple(buttons), page, total)

    def confirmation(self, *, title: str, body: str, action: UXAction,
                     entity_id: str, breadcrumbs: tuple[str, ...] = ()) -> UXPage:
        return UXPage(title, body, breadcrumbs, (
            UXButton("Confirm", f"ux:confirm:{action.value}:{entity_id}"),
            UXButton("Back", f"ux:back:{entity_id}"),
        ), 0, 1)
