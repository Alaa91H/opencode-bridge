"""Bounded ANSI screen model for the interactive Freebuff TUI.

Freebuff has no headless mode, so the bridge has to understand what the
terminal UI is showing. A full VT emulator would be ideal; this module keeps a
small, dependency-free model instead because the production environment pins the
runtime to three packages and forbids installing more. It intentionally ignores
cursor-addressed drawing: the bridge only needs the latest visible text, the
presence of known state markers, and a stable "no output" signal.
"""

from __future__ import annotations

import re
import time

CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
ESC_RE = re.compile(r"\x1b[@-Z\\-_]")
CONTROL_RE = re.compile(r"[\x00-\x07\x0b\x0c\x0e-\x1f\x7f]")

_LOGIN_MARKERS = (
    "please log in",
    "log in to freebuff",
    "freebuff login",
    "not logged in",
    "sign in to continue",
    "sign in to freebuff",
    "session expired",
)
_PROMPT_MARKERS = (
    "❯",
    "›",
    "ask anything",
    "type a message",
    "message freebuff",
    "describe a task",
)
_BUSY_MARKERS = (
    "esc to interrupt",
    "esc interrupt",
    "press esc",
    "interrupt",
    "thinking…",
    "thinking...",
    "working…",
    "working...",
    "generating",
    "running tool",
    "applying changes",
    "searching the web",
)
_ERROR_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"(?i)rate limit|too many requests|\b429\b"),
        "rate_limited",
        "تجاوز Freebuff حد الطلبات المؤقت. سيُعاد المحاولة تلقائياً بعد قليل.",
    ),
    (
        re.compile(
            r"(?i)out of free|payment required|\b403\b"
            r"|(?:quota|balance|hours).{0,40}(?:exceed|reached|out|empty|reset|limit)"
        ),
        "quota",
        "انتهت حصة Freebuff المتاحة لهذا الحساب حالياً. أعد المحاولة بعد تجديد الحصة.",
    ),
    (
        re.compile(r"(?i)invalid api key|unauthorized|\b401\b|session expired|please log in"),
        "auth",
        "انتهت صلاحية تسجيل الدخول إلى Freebuff. نفّذ freebuff login على الخادم ثم أعد المحاولة.",
    ),
    (
        re.compile(r"(?i)model .*unavailable|model not found|unknown model"),
        "model",
        "النموذج المختار في Freebuff غير متاح حالياً. اختر نموذجاً آخر من داخل CLI.",
    ),
)


def clean_ansi(text: str) -> str:
    """Remove terminal control sequences while keeping readable text."""

    without_osc = OSC_RE.sub("", text)
    without_csi = CSI_RE.sub("", without_osc)
    without_esc = ESC_RE.sub("", without_csi)
    return CONTROL_RE.sub("", without_esc)


class ScreenBuffer:
    """Keep a bounded tail of the visible terminal text."""

    def __init__(self, *, max_lines: int = 600, max_line_chars: int = 4000) -> None:
        self.max_lines = max(20, max_lines)
        self.max_line_chars = max(200, max_line_chars)
        self._lines: list[str] = []
        self._current = ""
        self._pending_cr = False

    def reset(self) -> None:
        self._lines.clear()
        self._current = ""
        self._pending_cr = False

    def feed(self, chunk: str | bytes) -> None:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        text = clean_ansi(chunk)
        for char in text:
            if char == "\n":
                self._pending_cr = False
                self._push_line()
            elif char == "\r":
                # A bare CR redraws the current line; CR immediately followed by
                # LF is an ordinary newline and must keep the line it ended.
                self._pending_cr = True
            elif char == "\b":
                self._flush_cr()
                self._current = self._current[:-1]
            elif char == "\t":
                self._flush_cr()
                self._current += "    "
            else:
                self._flush_cr()
                self._current += char
                if len(self._current) > self.max_line_chars:
                    self._current = self._current[-self.max_line_chars :]

    def _flush_cr(self) -> None:
        if self._pending_cr:
            self._current = ""
            self._pending_cr = False

    def _push_line(self) -> None:
        line = self._current.rstrip()
        self._current = ""
        if not line and self._lines and not self._lines[-1]:
            return
        self._lines.append(line)
        if len(self._lines) > self.max_lines:
            del self._lines[: len(self._lines) - self.max_lines]

    @property
    def lines(self) -> list[str]:
        return [*self._lines, self._current]

    @property
    def text(self) -> str:
        return "\n".join(self.lines[-160:])


def detect_login(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _LOGIN_MARKERS)


def detect_prompt(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _PROMPT_MARKERS)


def detect_busy(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _BUSY_MARKERS)


def detect_error(text: str) -> tuple[str, str] | None:
    for pattern, kind, message in _ERROR_RULES:
        if pattern.search(text):
            return kind, message
    return None


class ScreenAnalyzer:
    """Track screen content plus the last time the TUI produced output."""

    def __init__(self, buffer: ScreenBuffer | None = None) -> None:
        self.buffer = buffer or ScreenBuffer()
        self.last_output = time.monotonic()

    def feed(self, chunk: str | bytes, *, now: float | None = None) -> None:
        payload = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
        if not payload:
            return
        self.buffer.feed(payload)
        self.last_output = now if now is not None else time.monotonic()

    @property
    def text(self) -> str:
        return self.buffer.text

    @property
    def login_required(self) -> bool:
        return detect_login(self.buffer.text)

    @property
    def prompt_visible(self) -> bool:
        return detect_prompt(self.buffer.text)

    @property
    def busy(self) -> bool:
        return detect_busy(self.buffer.text)

    def error(self) -> tuple[str, str] | None:
        return detect_error(self.buffer.text)

    def idle_for(self, seconds: float, *, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        return current - self.last_output >= max(0.0, seconds)

    def tail(self, lines: int = 40) -> str:
        return "\n".join(self.buffer.lines[-max(1, lines) :])
