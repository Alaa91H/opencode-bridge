"""Owner-only runtime switches and validated Linux memory policy.

Writes require permission from the separate Telegram administrative guard.
The privileged systemd service reads values as DATA, never executes them.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

AUTOPILOT_MODES = ("off", "notify", "auto-safe", "maintenance-window", "emergency-stop")
MAINTENANCE_WINDOW_UTC = (8, 10)


class RuntimeControls:
    def __init__(self, root: Path) -> None:
        self.root = root

    def autopilot_mode(self) -> str:
        """Explicit policy takes precedence; old installations retain their switch."""
        path = self.root / "autopilot-mode"
        if path.is_symlink():
            raise ValueError("Symlink autopilot policy is not permitted")
        if path.exists():
            mode = path.read_text(encoding="ascii").strip()
            if mode not in AUTOPILOT_MODES:
                raise ValueError("Invalid autopilot mode")
            return mode
        previous = self.root / "auto-update-enabled"
        if previous.is_symlink():
            raise ValueError("Symlink legacy update policy is not permitted")
        return ("auto-safe" if previous.exists()
                and previous.read_text(encoding="ascii").strip() == "1" else "off")

    def set_autopilot_mode(self, mode: str) -> None:
        if mode not in AUTOPILOT_MODES:
            raise ValueError("Unsupported autopilot mode")
        from bridge.services.host_credentials import secure_write
        # Emergency stop also revokes the stored OS auto-update opt-in.
        # Exiting the stop never silently re-enables privileged host upgrades.
        if mode == "emergency-stop":
            secure_write(self.root / "host-auto-update-enabled", b"0\n")
        # Disable legacy automatic deployment before changing the new mode.
        # An interrupted update or rollback cannot accidentally re-enable it.
        legacy = self.root / "auto-update-enabled"
        secure_write(legacy, b"0\n")
        secure_write(self.root / "autopilot-mode", (mode + "\n").encode("ascii"))
        if mode in ("auto-safe", "maintenance-window"):
            secure_write(legacy, b"1\n")

    def auto_update(self) -> bool:
        return self.autopilot_mode() in ("auto-safe", "maintenance-window")

    def set_auto_update(self, enabled: bool) -> None:
        self.set_autopilot_mode("auto-safe" if enabled else "off")

    def memory(self) -> dict[str, int | str]:
        defaults: dict[str, int | str] = {"zram_percent": "auto", "swap_gib": "auto"}
        file = self.root / "memory-policy.json"
        if not file.exists():
            return defaults
        if file.is_symlink():
            raise RuntimeError("Symlink policy denied")
        data = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid memory policy")
        zram = data.get("zram_percent", "auto")
        swap = data.get("swap_gib", "auto")
        if zram != "auto" and (type(zram) is not int or zram not in (25, 50, 75, 100)):
            raise ValueError("Invalid ZRAM percentage")
        if (swap != "auto" and (type(swap) is not int or swap not in (0, 1, 2, 4))):
            raise ValueError("Invalid disk swap size")
        return {"zram_percent": zram, "swap_gib": swap}

    def set_memory(self, *, zram_percent: int | str | None = None,
                   swap_gib: int | str | None = None) -> dict[str, int | str]:
        value = self.memory()
        if zram_percent is not None:
            value["zram_percent"] = zram_percent
        if swap_gib is not None:
            value["swap_gib"] = swap_gib
        # Validate before writing.
        zram, swap = value["zram_percent"], value["swap_gib"]
        if zram != "auto" and (type(zram) is not int or zram not in (25, 50, 75, 100)):
            raise ValueError("Unsupported ZRAM setting")
        if swap != "auto" and (type(swap) is not int or swap not in (0, 1, 2, 4)):
            raise ValueError("Unsupported swap setting")
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.root / f".memory-policy.{os.getpid()}.tmp"
        try:
            with temp.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True)
            os.chmod(temp, 0o600)
            os.replace(temp, self.root / "memory-policy.json")
        finally:
            temp.unlink(missing_ok=True)
        return value

    def root_assets_stale(self) -> bool:
        """True while a deployment changed root-owned assets that root has not refreshed yet."""
        path = self.root / "root-assets-stale"
        return path.is_file() and not path.is_symlink()

    def last_release_operation(self):
        """Return the last recorded release phase without exposing secrets."""
        from bridge.services.update_coordinator import UpdateCoordinator

        return UpdateCoordinator(self.root).latest()

    def failed_update_release(self) -> str | None:
        """Expose a failed Bridge release to the private owner panel.

        A corrupt status file is not interpreted as a healthy update state.
        Do not show command output, environment variables, or secret material.
        """
        path = self.root / "auto-update-quarantine.json"
        if path.is_symlink():
            raise ValueError("Unsafe update quarantine status")
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ValueError("Invalid update quarantine status")
        tag = data.get("tag")
        if not isinstance(tag, str) or not re.fullmatch(
            r"v[0-9]+[.][0-9]+[.][0-9]+", tag,
        ):
            raise ValueError("Invalid update quarantine tag")
        return tag

    def system_auto_update(self) -> bool:
        """Host-wide APT operations are privileged and require explicit opt-in."""
        path = self.root / "host-auto-update-enabled"
        if path.is_symlink():
            raise ValueError("Unsafe host-update policy symlink")
        if not path.is_file():
            return False
        # Unknown contents never mean consent to privileged package changes.
        return path.read_text(encoding="ascii").strip() == "1"

    def set_system_auto_update(self, enabled: bool) -> None:
        if enabled and self.autopilot_mode() == "emergency-stop":
            raise ValueError("OS auto-update cannot be enabled during emergency stop")
        from bridge.services.host_credentials import secure_write
        secure_write(self.root / "host-auto-update-enabled", b"1\n" if enabled else b"0\n")

    def purge_backups_after_update(self) -> bool:
        path = self.root / "purge-backups-after-update"
        return path.is_file() and path.read_text(encoding="ascii").strip() == "1"

    def set_purge_backups_after_update(self, enabled: bool) -> None:
        from bridge.services.host_credentials import secure_write
        secure_write(self.root / "purge-backups-after-update", b"1\n" if enabled else b"0\n")
