"""Read deployment flags through the same env-over-dotenv precedence as Settings."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from bridge.config.settings import _parse_env_file

log = logging.getLogger("opencode_bridge.runtime_flags")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_TRUE = {'1', 'true', 'yes', 'on'}
_FALSE = {'0', 'false', 'no', 'off'}

# One warning per malformed flag name, so per-tick reads stay quiet afterwards.
_WARNED: set[str] = set()


def enabled(name: str, *, default: bool = False) -> bool:
    """Strict read: a malformed value raises so callers can fail closed loudly.

    Use this at startup for gates whose safe direction is to refuse (for
    example ``OPENCODE_FULL_ACCESS``), where a silent fallback would widen
    privileges. Long-lived task paths must use :func:`enabled_lenient`
    instead so a typo in ``.env`` cannot fail every task.
    """
    value = _raw(name)
    if value is None:
        return default
    val = value.strip().lower()
    if val not in _TRUE | _FALSE:
        raise ValueError(f'{name} must be a valid boolean setting')
    return val in _TRUE


def enabled_lenient(name: str, *, default: bool = False) -> bool:
    """Fail-soft read: a malformed value degrades to ``default`` with a warning.

    These flags only tune optional presentation, so a typo degrades the
    experience instead of bricking the bot; the reason stays diagnosable from
    the journal. The malformed value is remembered per name so a per-tick read
    logs once rather than once per task per tick.
    """
    try:
        return enabled(name, default=default)
    except ValueError as exc:
        if name not in _WARNED:
            _WARNED.add(name)
            log.warning("تجاهل قيمة غير صالحة لإعداد %s (%s)، سيُستخدم الافتراضي", name, exc)
        return default


def _raw(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        value = _parse_env_file(_PROJECT_ROOT / '.env').get(name)
    return value
