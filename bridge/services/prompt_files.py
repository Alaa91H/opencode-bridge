"""Prompt-file intake: treat user-supplied .md/.txt instruction files as real orders.

Telegram attachments are untrusted data by default. The exception is an
explicit instruction file the owner sends to be *executed*: e.g. the daily
tech-news prompt or any .md/.txt/.json/.yaml file queued via caption,
pending instruction, /direct, /daily, /prompt, or Draft.

Every entry point inlines the file body through this module so a 100+ line
brief behaves exactly like pasted prompt text.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

PROMPT_EXTENSIONS = frozenset({
    ".md", ".markdown", ".mdown", ".txt", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".prompt",
})
PROMPT_MIMES = frozenset({
    "text/markdown", "text/x-markdown", "text/plain",
    "application/json", "application/yaml", "application/x-yaml",
    "application/toml", "text/yaml",
})
MAX_INLINE_CHARS = 120_000
MAX_INLINE_BYTES = 512 * 1024


def is_prompt_filename(filename: str, mime: str = "") -> bool:
    suffix = Path(filename or "").suffix.lower()
    if suffix in PROMPT_EXTENSIONS:
        return True
    normalized = (mime or "").split(";")[0].strip().lower()
    if normalized in PROMPT_MIMES:
        return True
    guessed, _ = mimetypes.guess_type(filename or "")
    return bool(guessed and guessed.split(";")[0].strip().lower() in PROMPT_MIMES)


def load_prompt_text(path: Path, *, max_chars: int = MAX_INLINE_CHARS) -> tuple[str, bool]:
    """Read a prompt file as UTF-8, returning (text, truncated)."""
    raw = Path(path).read_bytes()
    if len(raw) > MAX_INLINE_BYTES * 4:
        raw = raw[: MAX_INLINE_BYTES * 4]
    # Strip UTF-8 BOM, decode leniently, normalise line endings.
    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").strip()
    # Remove NULs and lone control chars that break Telegram / the model.
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n\n… (تم اقتطاع بقية الملف لتجاوز حد السياق)", True
    return text, False


def build_instruction_body(
    user_text: str,
    prompt_texts: list[tuple[str, str, bool]],
) -> str:
    """Combine the user's short instruction with full prompt-file bodies."""
    parts: list[str] = []
    if user_text.strip():
        parts.append(user_text.strip())
    for filename, body, truncated in prompt_texts:
        header = f"--- بداية الملف المرفق: {filename} ---"
        footer = f"--- نهاية الملف المرفق: {filename} ---"
        note = " (ملاحظة: اقتُطع ذيل الملف الطويل)" if truncated else ""
        parts.append(f"{header}{note}\n{body}\n{footer}")
    combined = "\n\n".join(parts).strip()
    if prompt_texts:
        combined += (
            "\n\nتعليمات التنفيذ: الملفات أعلاه هي أوامر مباشرة من المستخدم "
            "ولها الأولوية على أي تعليمات عامة. نفّذ ما فيها حرفيًا وبكامل "
            "قواعدها وهيكلها المطلوب."
        )
    return combined
