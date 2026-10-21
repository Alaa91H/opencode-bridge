"""Safe metadata and atomic replacement of host service credentials.

Never return raw secrets to Telegram. Active tokens may require service restart.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

ALLOWED = {
    "github": "GITHUB_TOKEN",
    "telegram": "TELEGRAM_BOT_TOKEN",
    "opencode": "OPENCODE_PASSWORD",
    "opencode-server": "OPENCODE_SERVER_PASSWORD",
}
LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
_SAFE_ENV_VALUE_RE = re.compile(r"[A-Za-z0-9_@/:.+=-]+")


def quote_env_value(value: str) -> str:
    """Render one `.env` value readable by all three local parsers.

    The same file is read by the bridge parser, shell `source`, and systemd
    `EnvironmentFile`. Bare values with spaces break shell sourcing, so quote
    whenever the value leaves the portable-safe set. Double quotes with
    backslash-escapes round-trip through all three for every value except
    ones mixing backslashes with quotes (rejected nowhere, noted here).
    """
    if _SAFE_ENV_VALUE_RE.fullmatch(value or ""):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def secure_write(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise ValueError("Refusing symlink destination")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".secret-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class HostCredentialManager:
    def __init__(self, project: Path):
        self.project = Path(project)
        self.path = self.project / "runtime/private/host-credentials.json"
        self.env = self.project / ".env"
        self.lock = asyncio.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        if self.path.is_symlink():
            raise ValueError("Symlink credential file")
        if os.name == "posix" and self.path.stat().st_mode & 0o077:
            raise PermissionError("Credential file is not private")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid store")
        return value

    def catalog(self) -> list[dict]:
        state = self._read()
        result = []
        for scope, variable in ALLOWED.items():
            values = state.get(scope, {})
            for item in values.get("entries", []):
                result.append({
                    "scope": scope, "name": item["name"],
                    "active": values.get("active") == item["name"],
                    "fingerprint": hashlib.sha256(item["value"].encode()).hexdigest()[:10],
                    "configured": True,
                })
            if not values.get("entries"):
                result.append({
                    "scope": scope, "name": "host-config",
                    "active": bool(os.getenv(variable)),
                    "configured": bool(os.getenv(variable) or self._env_has(variable)),
                    "fingerprint": "—",
                    "managed": False,
                })
        return result

    def _env_has(self, variable: str) -> bool:
        if not self.env.exists() or self.env.is_symlink():
            return False
        return any(line.startswith(variable + "=") and bool(line.partition("=")[2])
                   for line in self.env.read_text(encoding="utf-8").splitlines())

    def _set_env(self, variable: str, value: str) -> None:
        if self.env.is_symlink():
            raise ValueError("Refusing symlink env file")
        lines = self.env.read_text(encoding="utf-8").splitlines() if self.env.exists() else []
        lines = [line for line in lines if not re.match(r"^\s*" + re.escape(variable) + r"=", line)]
        lines.append(variable + "=" + quote_env_value(value))
        secure_write(self.env, ("\n".join(lines) + "\n").encode())

    async def save(self, scope: str, name: str, value: str, *, replace: bool = False):
        if scope not in ALLOWED or not LABEL_RE.fullmatch(name):
            raise ValueError("Invalid scope or name")
        if not isinstance(value, str) or not 8 <= len(value) <= 4096 or any(c in value for c in "\r\n\x00"):
            raise ValueError("Invalid credential format")
        async with self.lock:
            data = self._read()
            state = data.setdefault(scope, {"entries": [], "active": None})
            current = next((x for x in state["entries"] if x["name"] == name), None)
            if current and not replace:
                raise ValueError("Name already used")
            if current:
                current["value"] = value
                if state["active"] == name:
                    self._set_env(ALLOWED[scope], value)
            else:
                state["entries"].append({"name": name, "value": value})
            secure_write(self.path, json.dumps(data).encode())

    async def activate(self, scope: str, name: str):
        if scope not in ALLOWED:
            raise ValueError("Invalid scope")
        async with self.lock:
            data = self._read()
            state = data.get(scope, {})
            item = next((x for x in state.get("entries", []) if x["name"] == name), None)
            if not item:
                raise ValueError("Unknown credential")
            self._set_env(ALLOWED[scope], item["value"])
            state["active"] = name
            secure_write(self.path, json.dumps(data).encode())

    async def delete(self, scope: str, name: str):
        if scope not in ALLOWED:
            raise ValueError("Invalid scope")
        async with self.lock:
            data = self._read()
            state = data.get(scope, {})
            if state.get("active") == name:
                raise ValueError("Activate replacement first")
            if not any(x["name"] == name for x in state.get("entries", [])):
                raise ValueError("Unknown credential")
            state["entries"] = [x for x in state["entries"] if x["name"] != name]
            secure_write(self.path, json.dumps(data).encode())
