"""Freebuff-specific failures (kept for import stability).

The canonical error types live in ``bridge.domain.agent.errors``; this module
re-exports the quota error so callers can import every Freebuff failure from one
place without depending on the implementation module.
"""

from __future__ import annotations

from bridge.infrastructure.freebuff.client import FreebuffQuotaError

__all__ = ["FreebuffQuotaError"]
