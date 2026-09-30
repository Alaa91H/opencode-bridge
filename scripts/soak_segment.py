#!/usr/bin/env python3
"""Run one bounded endurance segment and emit machine-verifiable evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from load_soak import run


async def execute(args: argparse.Namespace) -> dict[str, object]:
    started = datetime.now(UTC)
    result = await run(
        tasks=args.tasks,
        schedules=args.schedules,
        owners=args.owners,
        payload_bytes=args.payload_bytes,
        slow_ms=args.slow_ms,
        duration_seconds=args.duration_seconds,
    )
    finished = datetime.now(UTC)
    elapsed = float(result["elapsed_seconds"])
    if elapsed < args.duration_seconds * 0.995:
        raise RuntimeError(f"segment ended too early: {elapsed:.2f}s")
    if int(result["peak_memory_bytes"]) > args.max_peak_memory_bytes:
        raise RuntimeError(
            f"peak memory {result['peak_memory_bytes']} exceeds {args.max_peak_memory_bytes}"
        )
    return {
        "request_id": args.request_id,
        "source_sha": args.source_sha,
        "segment": args.segment,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "requested_seconds": args.duration_seconds,
        "elapsed_seconds": elapsed,
        "tasks": int(result["tasks"]),
        "peak_memory_bytes": int(result["peak_memory_bytes"]),
        "logical_payload_bytes": int(result["payload_bytes"]),
        "schedules": args.schedules,
        "owners": args.owners,
        "slow_ms": args.slow_ms,
        "completed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment", type=int, required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=14700)
    parser.add_argument("--tasks", type=int, default=2000)
    parser.add_argument("--schedules", type=int, default=200)
    parser.add_argument("--owners", type=int, default=50)
    parser.add_argument("--payload-bytes", type=int, default=4 * 1024**3)
    parser.add_argument("--slow-ms", type=float, default=250)
    parser.add_argument("--max-peak-memory-bytes", type=int, default=64 * 1024**2)
    args = parser.parse_args()
    evidence = asyncio.run(execute(args))
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
