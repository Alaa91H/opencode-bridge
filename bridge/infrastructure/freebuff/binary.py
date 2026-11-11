"""Locate and validate the Freebuff CLI without reading its credentials.

Only metadata is inspected: the binary path, its reported version, the config
directory and whether a credentials file exists. The token itself is never read,
logged or copied, and ``log.jsonl`` (which may contain a plaintext account key)
is never opened anywhere in this package.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

PROBE_TIMEOUT_SECONDS = 15.0
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_SAFE_ENV_KEYS = (
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    # Linux Secret Service / keyring access used by current Freebuff versions.
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "TMPDIR",
    "TEMP",
    "TMP",
    "USER",
    "LOGNAME",
)


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text or "")
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def version_at_least(version: tuple[int, int, int] | None, minimum: tuple[int, int, int]) -> bool:
    return version is not None and version >= minimum


def resolve_config_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "manicode"
    return Path.home() / ".config" / "manicode"


def credentials_path(config_dir: Path) -> Path:
    return config_dir / "credentials.json"


def credentials_present(config_dir: Path) -> bool:
    """Presence and shape only; the file content is never read."""

    path = credentials_path(config_dir)
    try:
        return path.is_file() and path.stat().st_size >= 2
    except OSError:
        return False


def resolve_binary(explicit: str | None, config_dir: Path) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    located = shutil.which("freebuff")
    if located:
        candidates.append(Path(located))
    candidates.extend(
        [
            config_dir / "freebuff",
            config_dir / "freebuff.exe",
            Path.home() / ".local" / "bin" / "freebuff",
            Path("/usr/local/bin/freebuff"),
        ]
    )
    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        except OSError:
            continue
    return None


def child_env(config_dir: Path | None = None) -> dict[str, str]:
    """A minimal environment: no bridge tokens, proxies or provider keys leak in."""

    env = {key: os.environ[key] for key in _SAFE_ENV_KEYS if os.environ.get(key)}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "140"
    env["LINES"] = "45"
    if config_dir is not None:
        env["FREEBUFF_CONFIG_DIR"] = str(config_dir)
    return env


@dataclass(frozen=True)
class ProbeConfig:
    binary: str = ""
    config_dir: str = ""
    min_version: str = "0.0.140"


@dataclass(frozen=True)
class FreebuffInstallation:
    path: Path | None
    version: tuple[int, int, int] | None
    version_text: str
    config_dir: Path
    credentials_present: bool
    supported: bool
    reason: str

    @property
    def healthy(self) -> bool:
        return bool(self.path is not None and self.supported and self.credentials_present)

    def health(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "backend": "freebuff",
            "version": self.version_text or "unknown",
            "binary": str(self.path) if self.path else "",
            "config_dir": str(self.config_dir),
            "logged_in": self.credentials_present,
            "supported": self.supported,
            "reason": self.reason,
        }


async def probe(config: ProbeConfig) -> FreebuffInstallation:
    config_dir = resolve_config_dir(config.config_dir or None)
    minimum = parse_version(config.min_version) or (0, 0, 140)
    binary = resolve_binary(config.binary or None, config_dir)
    logged_in = credentials_present(config_dir)
    if binary is None:
        return FreebuffInstallation(
            path=None,
            version=None,
            version_text="",
            config_dir=config_dir,
            credentials_present=logged_in,
            supported=False,
            reason="not_installed",
        )

    version_text = ""
    try:
        process = await asyncio.create_subprocess_exec(
            str(binary),
            "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=child_env(config_dir),
        )
    except OSError:
        return FreebuffInstallation(
            path=binary,
            version=None,
            version_text="",
            config_dir=config_dir,
            credentials_present=logged_in,
            supported=False,
            reason="not_executable",
        )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), PROBE_TIMEOUT_SECONDS)
        version_text = output.decode("utf-8", errors="replace").strip()
    except TimeoutError:
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), 5.0)
        except (TimeoutError, ProcessLookupError):
            pass
        return FreebuffInstallation(
            path=binary,
            version=None,
            version_text="",
            config_dir=config_dir,
            credentials_present=logged_in,
            supported=False,
            reason="probe_timeout",
        )

    version = parse_version(version_text)
    if version is None:
        return FreebuffInstallation(
            path=binary,
            version=None,
            version_text=version_text[:120],
            config_dir=config_dir,
            credentials_present=logged_in,
            supported=False,
            reason="version_unparseable",
        )
    if version < minimum:
        return FreebuffInstallation(
            path=binary,
            version=version,
            version_text=".".join(str(part) for part in version),
            config_dir=config_dir,
            credentials_present=logged_in,
            supported=False,
            reason="version_too_old",
        )
    return FreebuffInstallation(
        path=binary,
        version=version,
        version_text=".".join(str(part) for part in version),
        config_dir=config_dir,
        credentials_present=logged_in,
        supported=True,
        reason="ok",
    )
