"""Freebuff CLI backend infrastructure.

Exports are lazy so pure helpers (screen, chat store, extractor) can be imported
and tested without pulling in the HTTP/PTY client or its runtime dependencies.
"""

from __future__ import annotations

from typing import Any

__all__ = ["FreebuffBackend", "FreebuffInstallation", "ProbeConfig", "probe"]


def __getattr__(name: str) -> Any:
    if name == "FreebuffBackend":
        from bridge.infrastructure.freebuff.client import FreebuffBackend

        return FreebuffBackend
    if name in {"FreebuffInstallation", "ProbeConfig", "probe"}:
        from bridge.infrastructure.freebuff import binary

        return getattr(binary, name)
    raise AttributeError(name)
