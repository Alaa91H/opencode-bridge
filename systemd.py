#!/usr/bin/env python3
"""Install the prebuilt systemd user units shipped with this bridge.

This helper only copies unit files and reloads systemd. It never builds code,
installs Python packages, or changes the bridge environment file.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

UNIT_NAMES = (
    "opencode-serve.service",
    "opencode-bridge-telegram.service",
    "opencode-bridge.target",
)


def main() -> None:
    bridge_dir = pathlib.Path(__file__).resolve().parent
    source_dir = bridge_dir / "deploy"
    destination_dir = pathlib.Path.home() / ".config" / "systemd" / "user"
    destination_dir.mkdir(parents=True, exist_ok=True)

    for name in UNIT_NAMES:
        source = source_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"ملف الوحدة مفقود: {source}")
        destination = destination_dir / name
        shutil.copy2(source, destination)
        print(f"تم تثبيت: {destination}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "opencode-bridge.target"],
        check=True,
    )
    print("تم تحديث الوحدات. شغّل: systemctl --user restart opencode-bridge.target")


if __name__ == "__main__":
    main()
