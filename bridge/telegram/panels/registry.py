"""Typed panel primitives for the Telegram control surface.

Callback payloads stay short on purpose: Telegram allows only 64 bytes of
``callback_data``. Every screen therefore addresses itself by a short name and
an action by a short verb, never by a free-text value.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # router.py imports this module, so a runtime import would be circular.
    # The name is only needed to resolve the Renderer alias below.
    from bridge.telegram.panels.router import PanelContext

PREFIX = "pnl"
PATTERN = r"^pnl:"
NAVIGATE = "go"
ACT = "do"
RAW = "raw"
CONFIRM_YES = "yes"
CONFIRM_NO = "no"
MENU = "menu"
MAX_CALLBACK_BYTES = 64


class PanelError(RuntimeError):
    """Raised when a panel action cannot be resolved or executed."""


@dataclass(frozen=True)
class PanelAction:
    """One button. ``confirm`` turns the first press into a confirmation step."""

    label: str
    verb: str
    arg: str = ""
    confirm: str | None = None
    destructive: bool = False


@dataclass(frozen=True)
class PanelView:
    text: str
    rows: tuple[tuple[PanelAction, ...], ...]


Renderer = Callable[["PanelContext"], Awaitable[PanelView]]


@dataclass(frozen=True)
class Panel:
    name: str
    title: str
    render: Renderer
    needs: tuple[str, ...] = ()


def navigate_callback(target: str) -> str:
    return f"{PREFIX}:{NAVIGATE}:{target}"


def action_callback(screen: str, verb: str, arg: str = "") -> str:
    return f"{PREFIX}:{ACT}:{screen}:{verb}:{arg}" if arg else f"{PREFIX}:{ACT}:{screen}:{verb}"


def confirm_callback(screen: str, verb: str, arg: str = "") -> str:
    return action_callback(screen, verb, arg).replace(f":{ACT}:", f":{CONFIRM_YES}:", 1)


def cancel_callback(screen: str) -> str:
    return f"{PREFIX}:{CONFIRM_NO}:{screen}"


def encode(*parts: str) -> str:
    return ":".join((PREFIX, *parts))


def parse(data: str) -> list[str]:
    """Split a panel payload, rejecting anything that is not a panel callback."""
    if not isinstance(data, str) or not data.startswith(f"{PREFIX}:"):
        raise PanelError("not a panel callback")
    parts = data.split(":")
    if len(parts) < 2:
        raise PanelError("panel callback is missing a verb")
    return parts


def assert_short(*payloads: str) -> None:
    """Guard the 64-byte Telegram limit at the point the payload is built."""
    for payload in payloads:
        size = len(payload.encode("utf-8"))
        if size > MAX_CALLBACK_BYTES:
            raise PanelError(f"callback_data is {size} bytes, limit is {MAX_CALLBACK_BYTES}: {payload!r}")
