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
    ".md", ".markdown", ".mdown", ".txt", ".text", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".prompt", ".rst", ".adoc", ".org", ".log",
    ".csv", ".tsv", ".xml", ".sql",
})
PROMPT_MIMES = frozenset({
    "text/markdown", "text/x-markdown", "text/plain",
    "application/json", "application/yaml", "application/x-yaml",
    "application/toml", "text/yaml",
})
MAX_INLINE_CHARS = 120_000
MAX_INLINE_BYTES = 512 * 1024


def looks_like_text(path: Path, *, sample_bytes: int = 16384) -> bool:
    """Content sniff for prompt files with an unknown or missing extension.

    The owner asked for "txt, md, or any format": a readable text file is a
    prompt regardless of its suffix. Binary attachments keep their old role, so
    a photo, archive or PDF is never inlined as instructions.
    """
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(sample_bytes)
    except OSError:
        return False
    if not raw:
        return True
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in raw:
        return False
    for encoding in ("utf-8", "cp1256", "cp1252"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\r\t")
        return controls <= max(2, len(text) // 20)
    return False


def is_prompt_filename(filename: str, mime: str = "", path: Path | None = None) -> bool:
    suffix = Path(filename or "").suffix.lower()
    if suffix in PROMPT_EXTENSIONS:
        return True
    normalized = (mime or "").split(";")[0].strip().lower()
    if normalized in PROMPT_MIMES or normalized.startswith("text/"):
        return True
    guessed, _ = mimetypes.guess_type(filename or "")
    guessed = (guessed or "").split(";")[0].strip().lower()
    if guessed in PROMPT_MIMES or guessed.startswith("text/"):
        return True
    return path is not None and looks_like_text(path)


def load_prompt_text(path: Path, *, max_chars: int = MAX_INLINE_CHARS) -> tuple[str, bool]:
    """Read a prompt file as UTF-8, returning (text, truncated)."""
    raw = Path(path).read_bytes()
    if len(raw) > MAX_INLINE_BYTES * 4:
        raw = raw[: MAX_INLINE_BYTES * 4]
    # Honour UTF-16 BOMs, then UTF-8, then Arabic Windows (cp1256) and the
    # Western fallback, so a prompt written in any common editor is readable.
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        decoded = raw.decode("utf-16", errors="replace")
    else:
        decoded = ""
        for encoding in ("utf-8-sig", "cp1256", "cp1252"):
            try:
                decoded = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not decoded:
            decoded = raw.decode("utf-8", errors="replace")
    # Normalise line endings and strip a leading BOM if one survived.
    text = decoded.replace("\r\n", "\n").replace("\ufeff", "").strip()
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
