"""Arabic labels for the owner-facing tool manager."""

from __future__ import annotations

from bridge.services.tool_manager import ToolStatus

TOOL_NAMES = {
    "git": "Git",
    "gh": "GitHub CLI",
    "curl": "curl",
    "jq": "jq",
    "rg": "ripgrep",
    "yt-dlp": "yt-dlp",
    "aria2": "aria2",
    "ffmpeg": "FFmpeg",
    "age": "age",
    "poppler": "Poppler",
    "tesseract": "Tesseract",
}

TOOL_PURPOSES = {
    "git": "المستودعات والإصدارات",
    "gh": "إدارة GitHub ومتابعة CI",
    "curl": "نقل HTTP واستدعاء الواجهات",
    "jq": "قراءة JSON وتحويله",
    "rg": "بحث سريع في الملفات",
    "yt-dlp": "تنزيل الفيديو والصوت",
    "aria2": "تنزيلات متوازية",
    "ffmpeg": "فحص الوسائط وتحويلها",
    "age": "نسخ احتياطية مشفّرة",
    "poppler": "استخراج نصوص وصور PDF",
    "tesseract": "التعرّف الضوئي على الحروف",
}

STATE_TEXT = {
    "usable": "يعمل",
    "disabled": "معطّل",
    "missing": "غير مثبّت",
    "removed": "محذوف",
    "broken": "يحتاج إصلاحاً",
}


def tool_name(key: str) -> str:
    return TOOL_NAMES.get(key, key)


def tool_purpose(key: str) -> str:
    return TOOL_PURPOSES.get(key, "")


def state_text(status: ToolStatus) -> str:
    if status.enabled and status.installed:
        return STATE_TEXT["usable"]
    if not status.enabled:
        return STATE_TEXT["removed"] if not status.installed else STATE_TEXT["disabled"]
    return STATE_TEXT["missing"]


def size_text(size_kib: int | None) -> str:
    if size_kib is None:
        return "—"
    if size_kib < 1024:
        return f"{size_kib} KiB"
    return f"{size_kib / 1024:.1f} MiB"
