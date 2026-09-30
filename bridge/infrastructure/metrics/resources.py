"""Portable host resource sampling without mandatory third-party dependencies."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from bridge.infrastructure.metrics.observability import MetricsRegistry


def sample_resources(registry: MetricsRegistry, *, disk_path: Path = Path()) -> None:
    usage = shutil.disk_usage(disk_path)
    registry.set("disk_free_bytes", usage.free)
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        registry.set("memory_bytes", page_size * pages)
    except (AttributeError, ValueError, OSError):
        pass
    try:
        load1 = os.getloadavg()[0]
        cpu_count = os.cpu_count() or 1
        registry.set("cpu_percent", min(100.0, max(0.0, load1 / cpu_count * 100.0)))
    except (AttributeError, OSError):
        pass
