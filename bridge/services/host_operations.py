"""Mutual exclusion for destructive host operations.

Audit finding: the panel's update buttons were fire-and-forget. `_running_updates`
in the control UI only keeps task references alive; nothing stopped a second tap
from starting a second host operation while the first was still running:

* two `apt-get upgrade` runs contend on the dpkg lock and can leave a partially
  upgraded system;
* two `self_update.py --apply` units deploy the same release concurrently;
* a reboot or a service restart landing in the middle of an upgrade is exactly
  the failure that bricks a host.

So every destructive host action — updates, check, reboot, service restart,
cleanup — passes through this guard. Refusal is immediate and explained rather
than queued: silently stacking a second thirty-minute operation behind the first
is worse than telling the owner it is already running.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("opencode_bridge.host_operations")

BUSY_MESSAGE = (
    "توجد عملية مضيف جارية بالفعل ({current}). "
    "انتظر انتهائها قبل بدء عملية أخرى — تشغيل عمليتين معًا قد يضرّ بحالة النظام."
)

_LABELS = {
    "agent": "تحديث الوكيل",
    "system": "تحديث النظام",
    "bridge": "تحديث الجسر",
    "bridge-check": "فحص تحديث الجسر",
    "reboot": "إعادة تشغيل الخادم",
    "svcrestart": "إعادة تشغيل خدمة",
    "cleanup": "تنظيف الملفات",
}


def label_for(kind: str) -> str:
    return _LABELS.get(kind, kind)


class HostOperationGuard:
    """Allow one destructive host operation at a time."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current: str | None = None

    @property
    def current(self) -> str | None:
        """Human-readable label of the running operation, if any."""
        return label_for(self._current) if self._current else None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def run(self, kind: str, operation: Callable[[], Awaitable[str]]) -> str:
        """Run ``operation`` exclusively, or refuse immediately with a reason."""
        if self._lock.locked():
            busy = BUSY_MESSAGE.format(current=self.current or "غير معروفة")
            log.warning("رُفض تشغيل %s: %s", kind, self.current)
            return busy
        async with self._lock:
            self._current = kind
            try:
                return await operation()
            finally:
                # Released even when the operation raises: a failed update must
                # not permanently block the owner's next attempt.
                self._current = None


#: Process-wide guard: the control panel and the command layer share it.
HOST_OPERATIONS = HostOperationGuard()
