#!/usr/bin/env python3
"""Keep the production checkout immutable outside GitHub Actions deployment."""

from __future__ import annotations

import json
from urllib.parse import urlparse

EXPECTED_REPOSITORY = "alaa91h/opencode-bridge"


def normalize_github_repository(remote: str) -> str | None:
    value = remote.strip()
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    elif value.startswith(("https://", "http://", "ssh://")):
        parsed = urlparse(value)
        if (parsed.hostname or "").casefold() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    else:
        return None
    path = path.removesuffix(".git").strip("/")
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return f"{parts[0]}/{parts[1]}".casefold()


def update() -> dict[str, str]:
    """Disable mutable checkout updates; the CI artifact workflow owns deploys."""
    return {
        "status": "skipped",
        "reason": "production updates require a verified GitHub Actions artifact",
    }


def main() -> int:
    print(json.dumps(update(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
