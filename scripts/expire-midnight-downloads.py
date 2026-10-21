#!/usr/bin/env python3
"""Guarded midnight UTC cache cleanup. No root privileges or remote calls."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.services.cache_retention import midnight_cleanup
from bridge.services.download_service import DownloadError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path,
                        default=Path(__file__).resolve().parents[1])
    options = parser.parse_args()
    try:
        result = midnight_cleanup(options.project)
    except (OSError, ValueError, sqlite3.Error, DownloadError):
        print(json.dumps({"status": "error", "reason": "cleanup verification failed"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
