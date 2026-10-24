#!/usr/bin/python3
"""Fixed-argument APT helper for the Bridge tool manager.

This file is installed root-owned at /usr/local/sbin/opencode-bridge-tools.
The bridge user may invoke it through one sudoers rule, but it cannot add a
package, change a package name or pass shell text: the map below is the whole
interface.  Removing a core tool is refused here as well as in the service.
"""

from __future__ import annotations

import os
import subprocess
import sys

PACKAGES = {
    "git": "git",
    "gh": "gh",
    "curl": "curl",
    "jq": "jq",
    "rg": "ripgrep",
    "yt-dlp": "yt-dlp",
    "aria2": "aria2",
    "ffmpeg": "ffmpeg",
    "age": "age",
    "poppler": "poppler-utils",
    "tesseract": "tesseract-ocr",
}
CORE = frozenset({"git", "gh", "curl"})
ACTIONS = frozenset({"install", "update", "remove"})
APT = "/usr/bin/apt-get"
APT_ENV = {
    **os.environ,
    "DEBIAN_FRONTEND": "noninteractive",
    "APT_LISTCHANGES_FRONTEND": "none",
    "NEEDRESTART_MODE": "a",
}


def usage() -> int:
    print("usage: opencode-bridge-tools <install|update|remove> <tool-key>", file=sys.stderr)
    return 2


def run(argv: list[str], *, timeout: int) -> int:
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed argv built from the map above
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=timeout, env=APT_ENV,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"tool helper: {type(exc).__name__}", file=sys.stderr)
        return 1
    output = completed.stdout or ""
    # Keep the bridge's bounded capture useful without dumping an entire apt log.
    print(output[-6000:], end="" if output.endswith("\n") else "\n")
    return completed.returncode


def main(argv: list[str]) -> int:
    if os.geteuid() != 0:
        print("tool helper: root is required", file=sys.stderr)
        return 1
    if len(argv) != 3:
        return usage()
    action, key = argv[1], argv[2]
    package = PACKAGES.get(key)
    if action not in ACTIONS or package is None:
        return usage()
    if action == "remove" and key in CORE:
        print(f"tool helper: {key} is a core tool and cannot be removed", file=sys.stderr)
        return 2
    if action == "install":
        code = run([APT, "-o", "Dpkg::Lock::Timeout=300", "update"], timeout=300)
        if code != 0:
            return code
        return run(
            [APT, "-o", "Dpkg::Lock::Timeout=300", "install", "-y",
             "--no-install-recommends", package],
            timeout=780,
        )
    if action == "update":
        return run(
            [APT, "-o", "Dpkg::Lock::Timeout=300", "install", "-y", "--only-upgrade",
             "--no-install-recommends", package],
            timeout=780,
        )
    return run(
        [APT, "-o", "Dpkg::Lock::Timeout=300", "remove", "-y", "--no-install-recommends", package],
        timeout=240,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
