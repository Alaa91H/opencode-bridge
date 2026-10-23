"""Backward-compatibility shim: re-exports SessionStore from the domain layer.

The canonical implementation lives in
``bridge.domain.sessions.session_store``. This module exists so existing
importers (``bot.py``, ``maintenance/daily_agent_maintenance.py``, tests)
keep working without modification.
"""

from __future__ import annotations

from bridge.domain.sessions.session_store import DB_PATH, SessionStore, UserSession

__all__ = ["DB_PATH", "SessionStore", "UserSession"]
