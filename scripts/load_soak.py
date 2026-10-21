#!/usr/bin/env python3
"""Synthetic load/soak harness for queue/schedule/owner/media shapes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import time
import tracemalloc


async def run(tasks: int, schedules: int, owners: int, payload_bytes: int,
              slow_ms: float, duration_seconds: float) -> dict[str, float]:
    tracemalloc.start()
    start = time.monotonic()
    deadline = start + duration_seconds
    completed = 0
    checksum = hashlib.sha256()
    chunk = b"x" * min(payload_bytes, 64 * 1024)
    while completed < tasks or time.monotonic() < deadline:
        owner = completed % max(1, owners)
        schedule = completed % max(1, schedules)
        checksum.update(chunk)
        checksum.update(f"{owner}:{schedule}".encode())
        if slow_ms:
            await asyncio.sleep(slow_ms / 1000)
        completed += 1
        if completed >= tasks and duration_seconds <= 0:
            break
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "tasks": completed,
        "elapsed_seconds": time.monotonic() - start,
        "peak_memory_bytes": peak,
        "payload_bytes": payload_bytes,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", type=int, default=1000)
    p.add_argument("--schedules", type=int, default=100)
    p.add_argument("--owners", type=int, default=20)
    p.add_argument("--payload-bytes", type=int, default=1024 * 1024 * 1024)
    p.add_argument("--slow-ms", type=float, default=0)
    p.add_argument("--duration-seconds", type=float, default=0)
    args = p.parse_args()
    print(asyncio.run(run(**vars(args))))


if __name__ == "__main__":
    main()
