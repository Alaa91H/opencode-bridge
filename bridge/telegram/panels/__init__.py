"""Telegram control-panel primitives."""

from bridge.telegram.panels.registry import (
    ACT,
    CONFIRM_NO,
    CONFIRM_YES,
    MAX_CALLBACK_BYTES,
    MENU,
    NAVIGATE,
    PATTERN,
    PREFIX,
    Panel,
    PanelAction,
    PanelError,
    PanelView,
    action_callback,
    assert_short,
    cancel_callback,
    confirm_callback,
    navigate_callback,
    parse,
)
from bridge.telegram.panels.router import PanelContext, PanelRouter
from bridge.telegram.panels.screens import build_panels

__all__ = [
    "ACT",
    "CONFIRM_NO",
    "CONFIRM_YES",
    "MAX_CALLBACK_BYTES",
    "MENU",
    "NAVIGATE",
    "PATTERN",
    "PREFIX",
    "Panel",
    "PanelAction",
    "PanelContext",
    "PanelError",
    "PanelRouter",
    "PanelView",
    "action_callback",
    "assert_short",
    "build_panels",
    "cancel_callback",
    "confirm_callback",
    "navigate_callback",
    "parse",
]
