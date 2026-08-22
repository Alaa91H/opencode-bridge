"""Safe, user-facing progress tracking for OpenCode-backed Telegram tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
MAX_PROGRESS_EVENTS = 40
MAX_RENDERED_EVENTS = 8


@dataclass(frozen=True)
class ProgressEntry:
    created_at: datetime
    phase: str
    message: str
    kind: str = "info"


@dataclass
class TaskProgress:
    task_id: int
    owner_id: str
    chat_id: int
    phase: str = "queued"
    status: str = "queued"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    message_id: int | None = None
    entries: list[ProgressEntry] = field(default_factory=list)


class ProgressStore:
    """In-memory activity summaries for current and recently finished tasks."""

    def __init__(self) -> None:
        self._items: dict[int, TaskProgress] = {}

    def start(self, task_id: int, owner_id: str, chat_id: int) -> TaskProgress:
        progress = TaskProgress(task_id=task_id, owner_id=owner_id, chat_id=chat_id)
        self._items[task_id] = progress
        self.record(task_id, "started", "بلّشت المهمة، وعم حضّر الاتصال مع الوكيل.")
        return progress

    def get(self, task_id: int) -> TaskProgress | None:
        return self._items.get(task_id)

    def latest_for_owner(self, owner_id: str) -> TaskProgress | None:
        choices = [item for item in self._items.values() if item.owner_id == owner_id]
        return max(choices, key=lambda item: item.updated_at) if choices else None

    def set_message_id(self, task_id: int, message_id: int) -> None:
        progress = self._items.get(task_id)
        if progress:
            progress.message_id = message_id
            progress.updated_at = datetime.now(UTC)

    def record(self, task_id: int, phase: str, message: str, kind: str = "info") -> ProgressEntry | None:
        progress = self._items.get(task_id)
        if progress is None:
            return None
        entry = ProgressEntry(created_at=datetime.now(UTC), phase=phase, message=message[:260], kind=kind)
        progress.phase = phase
        progress.updated_at = entry.created_at
        progress.entries.append(entry)
        if len(progress.entries) > MAX_PROGRESS_EVENTS:
            del progress.entries[:-MAX_PROGRESS_EVENTS]
        return entry

    def finish(self, task_id: int, status: str, message: str, kind: str = "success") -> ProgressEntry | None:
        progress = self._items.get(task_id)
        if progress:
            progress.status = status
        return self.record(task_id, status, message, kind)


def _tool_label(value: Any) -> str:
    tool = str(value or "أداة تنفيذ").strip().lower()
    labels = {
        "bash": "أمرًا على الخادم",
        "read": "قراءة ملف",
        "write": "كتابة ملف",
        "edit": "تعديل ملف",
        "glob": "البحث عن ملفات",
        "grep": "البحث داخل الملفات",
        "webfetch": "قراءة صفحة ويب",
        "websearch": "بحثًا على الويب",
        "task": "مهمة فرعية",
    }
    return labels.get(tool, "أداة تنفيذ")


def summarize_agent_event(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Convert a normalized OpenCode event to a safe progress update.

    The returned message deliberately excludes prompts, command arguments, tool
    inputs, raw outputs, reasoning text, URLs, and any secret-bearing content.
    """
    event_type = str(event.get("type") or "")
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return None

    if event_type == "message.updated":
        info = properties.get("info")
        if isinstance(info, dict) and info.get("role") == "assistant":
            return ("processing", "الوكيل عم يحلّل المهمة ويحضّر الخطوة التالية.", "info")
        return None

    if event_type == "message.part.updated":
        part = properties.get("part")
        if not isinstance(part, dict):
            return None
        if part.get("type") != "tool":
            return None
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = str(state.get("status") or "")
        label = _tool_label(part.get("tool"))
        if status == "pending":
            return ("tool_pending", f"عم يجهّز {label}.", "info")
        if status == "running":
            return ("tool_running", f"عم ينفّذ {label}.", "info")
        if status == "completed":
            return ("tool_completed", f"خلص {label}.", "success")
        if status == "error":
            return ("tool_error", f"واجهت {label} مشكلة؛ عم يكمّل بمعالجة آمنة.", "warning")
        return None

    if event_type == "todo.updated":
        todos = properties.get("todos")
        if not isinstance(todos, list):
            return None
        total = len(todos)
        completed = sum(1 for todo in todos if isinstance(todo, dict) and todo.get("status") == "completed")
        active = sum(1 for todo in todos if isinstance(todo, dict) and todo.get("status") == "in_progress")
        return ("plan", f"تحديث خطة التنفيذ: {completed}/{total} خطوة مكتملة، و{active} قيد العمل.", "info")

    if event_type == "command.executed":
        return ("command", "تم تنفيذ خطوة تشغيلية ضمن المهمة.", "info")

    if event_type == "permission.updated":
        return ("approval", "الوكيل ينتظر قرارًا تشغيليًا قبل متابعة خطوة حساسة.", "warning")

    if event_type == "session.idle":
        return ("agent_idle", "الوكيل أنهى التنفيذ وعم نجهّز النتيجة.", "success")

    if event_type == "session.error":
        return ("agent_error", "حدثت مشكلة لدى الوكيل؛ عم نسجّلها ونعالج النتيجة بأمان.", "error")

    return None


def render_progress(progress: TaskProgress, detail: bool = False) -> str:
    """Render a compact Telegram-safe status message."""
    labels = {
        "queued": "بانتظار التنفيذ",
        "started": "قيد البدء",
        "processing": "قيد التحليل",
        "tool_pending": "تحضير خطوة",
        "tool_running": "قيد التنفيذ",
        "tool_completed": "استكمال خطوة",
        "plan": "تحديث الخطة",
        "command": "تنفيذ خطوة",
        "approval": "بانتظار قرار",
        "agent_idle": "تحضير النتيجة",
        "completed": "مكتملة",
        "failed": "فشلت",
        "cancelled": "ملغاة",
    }
    elapsed = max(0, int((progress.updated_at - progress.started_at).total_seconds()))
    lines = [
        f"تقدم المهمة #{progress.task_id}",
        f"• الحالة: {labels.get(progress.phase, progress.phase)}",
        f"• الزمن: {elapsed} ثانية",
    ]
    events = progress.entries[-MAX_RENDERED_EVENTS if detail else -3 :]
    if events:
        lines.append("\nآخر النشاط:")
        for entry in events:
            stamp = entry.created_at.strftime("%H:%M:%S UTC")
            lines.append(f"• [{stamp}] {entry.message}")
    return "\n".join(lines)


def serialize_progress(progress: TaskProgress) -> list[dict[str, str]]:
    """Return only the safe fields intended for persistent task activity."""
    return [
        {
            "time": entry.created_at.isoformat(),
            "phase": entry.phase,
            "message": entry.message,
            "kind": entry.kind,
        }
        for entry in progress.entries
    ]


def render_persisted_activity(task_id: int, status: str, activity: tuple[dict[str, Any], ...], detail: bool = True) -> str:
    """Render activity stored in SQLite without reconstructing private agent data."""
    labels = {
        "queued": "بانتظار التنفيذ",
        "scheduled": "مجدولة",
        "running": "قيد التنفيذ",
        "completed": "مكتملة",
        "failed": "فشلت",
        "cancelled": "ملغاة",
    }
    lines = [f"سجل المهمة #{task_id}", f"• الحالة: {labels.get(status, status)}"]
    safe_entries = [item for item in activity if isinstance(item, dict)][-MAX_RENDERED_EVENTS if detail else -3 :]
    if not safe_entries:
        lines.append("\nلا يوجد نشاط مفصل محفوظ لهذه المهمة بعد.")
        return "\n".join(lines)
    lines.append("\nسجل التنفيذ:")
    for item in safe_entries:
        raw_time = str(item.get("time") or "")
        stamp = raw_time[11:19] + " UTC" if len(raw_time) >= 19 else "الآن"
        message = str(item.get("message") or "تحديث تنفيذ")[:260]
        lines.append(f"• [{stamp}] {message}")
    return "\n".join(lines)
