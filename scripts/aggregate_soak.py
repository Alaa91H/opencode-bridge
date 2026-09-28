#!/usr/bin/env python3
"""Aggregate T50 soak segments and fail unless 24-72 active hours completed."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def aggregate(root: Path) -> dict[str, object]:
    files = sorted(root.glob("segment-*.json"))
    if len(files) != 6:
        raise RuntimeError(f"expected 6 soak segments, found {len(files)}")
    segments = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    request_ids = {str(item.get("request_id", "")) for item in segments}
    if len(request_ids) != 1 or "" in request_ids:
        raise RuntimeError(f"invalid/mixed soak request ids: {sorted(request_ids)}")
    request_id = next(iter(request_ids))
    source_shas = {str(item.get("source_sha", "")) for item in segments}
    if len(source_shas) != 1 or "" in source_shas:
        raise RuntimeError(f"invalid/mixed soak source SHAs: {sorted(source_shas)}")
    source_sha = next(iter(source_shas))
    if not all(item.get("completed") is True for item in segments):
        raise RuntimeError("one or more soak segments are incomplete")
    elapsed = sum(float(item["elapsed_seconds"]) for item in segments)
    hours = elapsed / 3600.0
    if not 24 <= hours <= 72:
        raise RuntimeError(f"aggregate soak duration outside 24-72h: {hours:.3f}h")
    max_peak = max(int(item["peak_memory_bytes"]) for item in segments)
    return {
        "request_id": request_id,
        "source_sha": source_sha,
        "completed": True,
        "duration_hours": hours,
        "active_seconds": elapsed,
        "segments": len(segments),
        "max_peak_memory_bytes": max_peak,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "segment_evidence": segments,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = aggregate(args.root)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in ("completed", "duration_hours", "segments", "max_peak_memory_bytes")}))


if __name__ == "__main__":
    main()
