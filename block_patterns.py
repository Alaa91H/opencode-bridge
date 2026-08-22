"""Command-policy helpers for the Telegram bridge.

The OpenCode service enforces the same rules at tool-execution time.  These
checks provide immediate Arabic feedback when a user explicitly requests a
server-side build command.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

_RE_FLAGS = re.IGNORECASE | re.MULTILINE

# Commands which consume resources or generate artifacts and must never run on
# the production server. Patterns deliberately begin at a shell-command
# boundary so normal prose such as "make a plan" is not blocked.
BUILD_PATTERNS: list[tuple[str, str]] = [
    (r"^\s*(?:sudo\s+)?(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:build|compile|dist|prod)\b", "بناء حزمة JavaScript"),
    (r"^\s*(?:sudo\s+)?npx\s+(?:webpack|rollup|vite\s+build|esbuild|tsc|next\s+build|nuxt\s+build|gatsby\s+build)\b", "بناء حزمة JavaScript"),
    (r"^\s*(?:sudo\s+)?(?:make|cmake|ninja)\b", "بناء عبر Make أو CMake"),
    (r"^\s*(?:sudo\s+)?cargo\s+(?:build|install|package|release)\b", "بناء أو تثبيت Rust"),
    (r"^\s*(?:sudo\s+)?go\s+(?:build|install)\b", "بناء أو تثبيت Go"),
    (r"^\s*(?:sudo\s+)?(?:mvn|maven|gradle|\.\/gradlew|javac)\b", "بناء Java"),
    (r"^\s*(?:sudo\s+)?docker(?:\s+compose|-compose)?\s+build\b", "بناء صورة Docker"),
    (r"^\s*(?:sudo\s+)?(?:pip|pip3|python[23]?\s+-m\s+pip)\s+install\b", "تثبيت حزم Python على الخادم"),
    (r"^\s*(?:sudo\s+)?(?:flutter\s+build|dotnet\s+(?:build|publish))\b", "بناء تطبيق"),
]

# Irreversible or service-destroying actions remain prohibited even when the
# operator asks the build agent to act autonomously.
HARDLINE_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[^\s]*\s+)*(?:/|/\*|/home(?:/\*)?|/root(?:/\*)?|/etc(?:/\*)?)(?:\s|$)", "حذف مسار نظامي"),
    (r"\bmkfs(?:\.[a-z0-9]+)?\b", "تهيئة نظام ملفات"),
    (r"\bdd\b[^\n]*\bof=/dev/(?:sd|nvme|hd|mmcblk|vd|xvd)", "كتابة مباشرة على قرص"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "قنبلة عمليات"),
    (r"(?:^|[;&|\n])\s*(?:sudo\s+)?(?:shutdown|reboot|halt|poweroff)\b", "إيقاف أو إعادة تشغيل النظام"),
    (r"(?:^|[;&|\n])\s*(?:sudo\s+)?systemctl\s+(?:poweroff|reboot|halt|kexec)\b", "إيقاف أو إعادة تشغيل النظام"),
]

BUILD_COMPILED = [(re.compile(pattern, _RE_FLAGS), description) for pattern, description in BUILD_PATTERNS]
HARDLINE_COMPILED = [(re.compile(pattern, _RE_FLAGS), description) for pattern, description in HARDLINE_PATTERNS]


def _normalize(value: str) -> str:
    value = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", value)
    return unicodedata.normalize("NFKC", value.replace("\x00", ""))


def check_build(command: str) -> Optional[str]:
    """Return a localized reason if *command* is a prohibited build action."""
    normalized = _normalize(command)
    for pattern, description in BUILD_COMPILED:
        if pattern.search(normalized):
            return description
    return None


def check_hardline(command: str) -> Optional[str]:
    """Return a localized reason for a non-recoverable command."""
    normalized = _normalize(command)
    for pattern, description in HARDLINE_COMPILED:
        if pattern.search(normalized):
            return description
    return None


def check_command(command: str) -> tuple[Optional[str], bool, bool]:
    """Compatibility API: (reason, blocked, dangerous)."""
    reason = check_hardline(command)
    if reason:
        return reason, True, True
    reason = check_build(command)
    return reason, bool(reason), False
