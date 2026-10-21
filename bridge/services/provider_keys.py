"""Persistent provider-specific API key inventory for an owner-controlled bot.

Secrets never appear in list/status results or logs. Only a user-restricted
runtime file (0600) stores the keys. The active key is applied using OpenCode's
documented PUT /auth/{provider} API, not by replacing the bridge's Basic auth.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def validate_name(value: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError("provider and key labels must be alphanumeric identifiers")
    return value


class ProviderKeyManager:
    def __init__(self, path: Path, client: Any) -> None:
        self.path = path
        self.client = client
        self._lock = asyncio.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        if self.path.is_symlink():
            raise RuntimeError("Refusing symlink credential store")
        info = self.path.stat()
        if os.name == "posix" and info.st_mode & 0o077:
            raise PermissionError("Credential store permissions are too permissive")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid credential format")
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        finally:
            tmp.unlink(missing_ok=True)

    def catalog(self) -> list[dict]:
        """Public metadata only, ordered for stable Telegram button indexes."""
        result = []
        for provider, value in sorted(self._read().items()):
            if not isinstance(value, dict):
                continue
            for item in value.get("keys", []):
                if not isinstance(item, dict):
                    continue
                secret = item.get("secret", "")
                if not isinstance(secret, str):
                    continue
                result.append({
                    "provider": provider, "name": item.get("name", ""),
                    "fingerprint": hashlib.sha256(secret.encode()).hexdigest()[:10],
                    "active": value.get("active") == item.get("name"),
                    "auto": bool(value.get("auto", False)),
                })
        return result

    async def add(self, provider: str, name: str, secret: str) -> None:
        validate_name(provider)
        validate_name(name)
        if not isinstance(secret, str) or not 8 <= len(secret) <= 8192 or "\n" in secret:
            raise ValueError("Invalid credential length or format")
        async with self._lock:
            data = self._read()
            state = data.setdefault(provider, {"active": None, "auto": False, "keys": []})
            if any(key["name"] == name for key in state["keys"]):
                raise ValueError("Key label already exists")
            state["keys"].append({"name": name, "secret": secret})
            self._save(data)

    async def activate(self, provider: str, name: str) -> None:
        validate_name(provider)
        validate_name(name)
        async with self._lock:
            data = self._read()
            state = data.get(provider, {})
            key = next((key["secret"] for key in state.get("keys", [])
                        if key.get("name") == name), None)
            if key is None:
                raise ValueError("Unknown key label")
            # Persist selection only AFTER OpenCode accepts it.
            await self.client.set_provider_key(provider, key)
            state["active"] = name
            self._save(data)

    async def remove(self, provider: str, name: str) -> None:
        validate_name(provider)
        validate_name(name)
        async with self._lock:
            data = self._read()
            state = data.get(provider)
            if not state or not any(x.get("name") == name for x in state["keys"]):
                raise ValueError("Unknown key")
            if state.get("active") == name:
                raise ValueError("Select a replacement key before deleting the active key")
            state["keys"] = [x for x in state["keys"] if x["name"] != name]
            if not state["keys"]:
                del data[provider]
            self._save(data)

    async def toggle_auto(self, provider: str) -> bool:
        validate_name(provider)
        async with self._lock:
            data = self._read()
            if provider not in data:
                raise ValueError("No keys for provider")
            state = data[provider]
            state["auto"] = not state.get("auto", False)
            self._save(data)
            return state["auto"]

    async def rotate_on_error(self, provider: str, *, quota_scope: str = "credential") -> bool:
        if quota_scope != "credential":
            return False
        validate_name(provider)
        async with self._lock:
            data = self._read()
            state = data.get(provider, {})
            if not state.get("auto") or len(state.get("keys", [])) < 2:
                return False
            names = [item["name"] for item in state["keys"]]
            old = state.get("active")
            target = names[(names.index(old) + 1) % len(names)] if old in names else names[0]
            key = next(x["secret"] for x in state["keys"] if x["name"] == target)
            await self.client.set_provider_key(provider, key)
            state["active"] = target
            self._save(data)
            return True
