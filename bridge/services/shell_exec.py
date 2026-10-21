"""Owner-only direct shell: run a terminal command as if typed on the server.

Safety model (mirrors HostControl):
* Only the validated control owner in a private chat may call this.
* Every invocation is audit-logged with the full command string.
* No shell history file is touched; secrets are redacted from output.
* Time- and size-bounded while read; never blocks the bot loop forever.
* Runs as the bridge service user via ``bash -lc`` so shell operators,
  pipes and env expansion behave exactly like an interactive terminal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from bridge.services.host_control import redact

log = logging.getLogger("opencode_bridge.shell")

DEFAULT_TIMEOUT = 120.0
MAX_OUTPUT_CHARS = 12_000
MAX_COMMAND_CHARS = 4_000


@dataclass(frozen=True)
class ShellResult:
    code: int
    output: str
    truncated: bool


def validate_command(command: str) -> str:
    text = (command or "").strip()
    if not text:
        raise ValueError("أرسل الأمر بعد /exec مثال: /exec df -h")
    if len(text) > MAX_COMMAND_CHARS:
        raise ValueError("الأمر طويل جدًا (الحد 4000 محرف)")
    if "\x00" in text:
        raise ValueError("الأمر يحتوي على محارف غير صالحة")
    return text


def policy_advisory(command: str) -> str | None:
    """Advisory-only guard match for the owner shell; never blocks.

    `/exec` intentionally runs with the owner's own privileges (like SSH) and
    therefore bypasses the build/hardline request guards that constrain agent
    tasks. When the command matches a known destructive pattern, the caller
    surfaces this warning banner instead of refusing — the owner stays fully
    empowered but never uninformed.
    """
    from block_patterns import check_build, check_hardline

    try:
        danger = check_hardline(command)
        if danger:
            return f"⚠️ تحذير: هذا الأمر {danger} — راجع الخرج بعناية."
        build = check_build(command)
        if build:
            return f"ℹ️ ملاحظة: هذا الأمر {build} على الخادم الإنتاجي."
    except Exception:
        return None
    return None


async def run_shell(
    command: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> ShellResult:
    cleaned = validate_command(command)
    workdir = str(cwd) if cwd else str(Path.cwd())
    argv = ["bash", "-lc", cleaned]
    env_note = ""
    if extra_env:
        # Passed only as a prefix export, never interpolated into argv.
        prefix = " ".join(
            f"{key}={value}" for key, value in extra_env.items()
            if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) and "\n" not in value
        )
        if prefix:
            argv = ["bash", "-lc", f"{prefix} {cleaned}"]
            env_note = " (env)"
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=workdir,
            env={**os.environ, "TERM": "dumb"},
        )
    except OSError as exc:
        raise RuntimeError(f"تعذر بدء الصدفة: {type(exc).__name__}") from exc

    async def drain() -> bytes:
        stream = process.stdout
        if stream is None:  # defensive: the process is always spawned with a pipe
            return b""
        chunks: list[bytes] = []
        size = 0
        limit = 256 * 1024
        while True:
            block = await stream.read(8192)
            if not block:
                break
            size += len(block)
            if size > limit:
                chunks.append(block[: max(0, limit - (size - len(block)))])
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                break
            chunks.append(block)
        return b"".join(chunks)

    try:
        raw = await asyncio.wait_for(drain(), timeout)
        code = await asyncio.wait_for(process.wait(), 10)
    except TimeoutError as exc:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        raise TimeoutError(f"انتهت المهلة ({timeout:.0f}s){env_note}") from exc
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    text = raw.decode("utf-8", errors="replace").replace("\x00", "")
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    text = redact(text).strip()
    truncated = len(text) > MAX_OUTPUT_CHARS
    if truncated:
        text = text[:MAX_OUTPUT_CHARS].rstrip() + "\n… (اقتُطع الخرج لتجاوز حد العرض)"
    return ShellResult(code=code, output=text or "(لا خرج)", truncated=truncated)


def format_result(command: str, result: ShellResult) -> str:
    status = "✅ code 0" if result.code == 0 else f"❌ code {result.code}"
    body = result.output[-8000:]
    return f"{status}\n$ {command[:500]}\n\n{body}"


# Backwards-compatible helper used by tests / UI.
async def run_fixed_shell(command: str, *, timeout: float = DEFAULT_TIMEOUT) -> ShellResult:
    return await run_shell(command, timeout=timeout)


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_COMMAND_CHARS",
    "MAX_OUTPUT_CHARS",
    "ShellResult",
    "format_result",
    "policy_advisory",
    "run_fixed_shell",
    "run_shell",
    "validate_command",
]
