#!/usr/bin/env python3
"""Validate stable release version selection without GitHub event context."""

from __future__ import annotations

import re
import sys

STABLE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
PREVIEW = re.compile(r"2\.0\.0-rc\.[0-9]+\Z")


def classify(version: str) -> str:
    normalized = version.strip()
    if PREVIEW.fullmatch(normalized):
        return "preview"
    if STABLE.fullmatch(normalized):
        return "stable"
    raise ValueError(f"Invalid release version: {normalized}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: release_version.py VERSION", file=sys.stderr)
        return 2
    try:
        result = classify(sys.argv[1])
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
