"""Professional agent output formatting for Telegram long messages + images."""
from __future__ import annotations

import re
from typing import Any

MAX_MESSAGE_LENGTH = 4000


def format_agent_output(text: str, include_images: bool = False) -> str:
    """Clean, ordered, professional Arabic/English output for Telegram."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        line = line.rstrip()
        if line.startswith("  ") or line.startswith("\t"):
            line = line.lstrip()
        cleaned.append(line)
    result = "\n".join(cleaned)
    # Trim if too long for Telegram single message
    if len(result) > MAX_MESSAGE_LENGTH:
        result = result[:MAX_MESSAGE_LENGTH] + "\n\n... [استمر في قراءة النص الكامل عبر المستند]"
    return result


def chunk_for_telegram(text: str, chunk_size: int = 3500) -> list[str]:
    """Split very long output into sequential Telegram messages."""
    chunks: list[str] = []
    current = []
    current_len = 0
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) + 2 > chunk_size:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text[:chunk_size]]


def render_agency_message(message_text: str, task_id: int | None = None, status: str = "completed") -> str:
    header = "🤖 **إخراج الوكيل**"
    if task_id:
        header += f" | مهمة #{task_id}"
    header += f" | حالة: {status}"
    footer = "\n---\n📌 تم تقديم هذه النتيجة عبر النظام المتكامل | يمكن طلب التفاصيل عبر /taskshow"
    body = format_agent_output(message_text)
    full = f"{header}\n\n{body}{footer}"
    return full


def format_news_digest(news_items: list[dict[str, Any]]) -> str:
    lines = ["📰 **نشرة الأخبار — ملخص محترف**"]
    for item in news_items[:10]:
        title = item.get("title", "")[:120]
        source = item.get("source", "")
        lines.append(f"• {title} — _{source}_")
    return "\n".join(lines) + "\n\n---\n📌 تم إعداد هذا الملخص عبر الوكيل المتكامل"
