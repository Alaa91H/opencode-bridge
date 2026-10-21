"""Cards for the two-way conversation: the agent asks, Telegram answers.

A permission request and a question are the only moments where the agent stops
and waits for a human. Until now the bridge printed a line about it and moved
on, so the task hung until the server timed out. These functions turn either
event into a card with buttons, and build the button payloads back into values
the client accepts.

Telegram caps callback data at 64 bytes, so a payload carries only short
identifiers and indexes: the label behind an index is read back from the server,
never smuggled through the button.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

PREFIX = "ag"
CALLBACK_PATTERN = r"^ag:"
MAX_CALLBACK_BYTES = 64

PERMISSION_ONCE = "p1"
PERMISSION_ALWAYS = "pa"
PERMISSION_REJECT = "px"
QUESTION_OPTION = "qo"
QUESTION_TEXT = "qt"
QUESTION_CONFIRM = "qc"
QUESTION_REJECT = "qr"

PERMISSION_LABELS = {
    "bash": "تشغيل أمر على الخادم",
    "edit": "تعديل ملف",
    "write": "كتابة ملف",
    "read": "قراءة ملف",
    "webfetch": "جلب رابط من الإنترنت",
    "websearch": "بحث في الإنترنت",
    "task": "تشغيل وكيل فرعي",
    "external_directory": "العمل خارج مجلد المشروع",
    "todowrite": "تحديث خطة العمل",
    "question": "سؤال داخل المهمة",
}

QUESTION_ACTION_LABELS = {
    "bash": "تشغيل أمر على الخادم",
    "edit": "تعديل ملف",
    "write": "كتابة ملف",
    "read": "قراءة ملف",
    "webfetch": "جلب رابط",
    "websearch": "بحث",
    "glob": "استعراض ملفات",
    "grep": "بحث في الكود",
    "list": "استعراض مجلد",
    "task": "وكيل فرعي",
}

MAX_TARGETS = 4
MAX_OPTIONS = 6
MAX_TEXT_CHARS = 900

_PERMISSION_ID = re.compile(r"^per[A-Za-z0-9_-]{1,63}$")
_QUESTION_ID = re.compile(r"^que[A-Za-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class Question:
    """One question the agent asked, with the choices it offered."""

    header: str
    question: str
    options: tuple[tuple[str, str], ...] = ()
    multiple: bool = False
    custom: bool = False

    @property
    def needs_text(self) -> bool:
        return self.custom or not self.options


@dataclass(frozen=True)
class PermissionCard:
    request_id: str
    action: str
    targets: tuple[str, ...] = ()


def _clean(value: object, limit: int = 200) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def permission_payload(request_id: str, decision: str) -> str:
    codes = {
        PERMISSION_ONCE: "once",
        PERMISSION_ALWAYS: "always",
        PERMISSION_REJECT: "reject",
    }
    if decision not in codes:
        raise ValueError("Unknown permission decision")
    return _payload(f"p:{request_id}:{codes[decision]}")


def option_payload(request_id: str, question_index: int, option_index: int) -> str:
    return _payload(f"o:{request_id}:{question_index}:{option_index}")


def question_payload(request_id: str, action: str) -> str:
    if action not in {QUESTION_TEXT, QUESTION_CONFIRM, QUESTION_REJECT}:
        raise ValueError("Unknown question action")
    return _payload(f"q:{request_id}:{action[-1]}")


def _payload(body: str) -> str:
    payload = f"{PREFIX}:{body}"
    if len(payload.encode()) > MAX_CALLBACK_BYTES:
        raise ValueError("Callback payload exceeds the Telegram limit")
    return payload


def parse_payload(data: str) -> tuple[str, ...]:
    """Split a button payload back into its parts, or raise if it is foreign.

    Each route validates the identifier's own prefix, so a permission id cannot
    be replayed on the question route or the other way round.
    """
    if not isinstance(data, str) or not data.startswith(f"{PREFIX}:"):
        raise ValueError("Foreign callback payload")
    parts = data.split(":")
    if len(parts) < 3:
        raise ValueError("Incomplete callback payload")
    if parts[1] == "p" and len(parts) == 4:
        request_id, decision = parts[2], parts[3]
        if _PERMISSION_ID.match(request_id) and decision in {"once", "always", "reject"}:
            return ("permission", request_id, decision)
    if parts[1] == "o" and len(parts) == 5:
        request_id, question, option = parts[2], parts[3], parts[4]
        if _QUESTION_ID.match(request_id) and question.isdigit() and option.isdigit():
            return ("option", request_id, question, option)
    if parts[1] == "q" and len(parts) == 4:
        request_id, action = parts[2], parts[3]
        if _QUESTION_ID.match(request_id) and action in {"t", "c", "r"}:
            return ("question", request_id, action)
    raise ValueError("Unrecognized callback payload")


def permission_card(card: PermissionCard) -> str:
    """The question an owner reads before letting the agent act."""
    action = _clean(card.action, 60)
    title = PERMISSION_LABELS.get(action, QUESTION_ACTION_LABELS.get(action, action or "إجراء"))
    lines = [
        "<b>🔐 الوكيل يطلب إذنًا</b>",
        f"<b>الإجراء:</b> {html.escape(title)}",
    ]
    if card.targets:
        shown = card.targets[:MAX_TARGETS]
        lines.append(f"<b>المستهدف:</b> <code>{html.escape(shown[0])}</code>")
        for extra in shown[1:]:
            lines.append(f"• <code>{html.escape(extra)}</code>")
        remaining = len(card.targets) - len(shown)
        if remaining > 0:
            lines.append(f"• … و{remaining} مستهدف آخر")
    lines.append("")
    lines.append("اختر الرد، والمهمة تستأنف فورًا.")
    return "\n".join(lines)


def permission_keyboard(card: PermissionCard) -> list[list[tuple[str, str]]]:
    request_id = card.request_id
    return [
        [
            ("✅ موافقة مرة واحدة", permission_payload(request_id, PERMISSION_ONCE)),
            ("♾️ اسمح دائمًا", permission_payload(request_id, PERMISSION_ALWAYS)),
        ],
        [("⛔ رفض", permission_payload(request_id, PERMISSION_REJECT))],
    ]


def question_card(questions: tuple[Question, ...]) -> str:
    """The agent's own question, verbatim enough to answer without guessing."""
    lines = ["<b>❓ الوكيل يسألك</b>"]
    for index, item in enumerate(questions[:3], start=1):
        header = _clean(item.header, 40)
        question = _clean(item.question, MAX_TEXT_CHARS)
        prefix = f"{header}: " if header else ""
        lines.append("")
        lines.append(f"<b>{index}. {html.escape(prefix + question)}</b>")
        for label, description in item.options[:MAX_OPTIONS]:
            text = _clean(label, 40)
            note = _clean(description, 120)
            body = f"{text} — {note}" if note else text
            lines.append(f"• {html.escape(body)}")
        if item.needs_text:
            lines.append("• يمكنك الكتابة كإجابة نصية.")
    lines.append("")
    lines.append("اضغط الخيار المناسب، والمهمة تستأنف فورًا.")
    return "\n".join(lines)


def question_keyboard(
    request_id: str,
    questions: tuple[Question, ...],
    chosen: dict[int, set[str]] | None = None,
) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    picked = chosen or {}
    for question_index, item in enumerate(questions[:3]):
        selected = picked.get(question_index, set())
        buttons = [
            (
                f"✔ {_clean(label, 40)}" if label in selected else _clean(label, 40),
                option_payload(request_id, question_index, option_index),
            )
            for option_index, (label, _description) in enumerate(item.options[:MAX_OPTIONS])
        ]
        if buttons:
            rows.append(buttons)
    if questions and any(item.multiple for item in questions):
        rows.append([("✅ تأكيد الاختيار", question_payload(request_id, QUESTION_CONFIRM))])
    if questions and any(item.needs_text for item in questions):
        rows.append([("✍️ إجابة نصية", question_payload(request_id, QUESTION_TEXT))])
    rows.append([("⛔ إلغاء السؤال", question_payload(request_id, QUESTION_REJECT))])
    return rows


def answered_note(kind: str, detail: str = "") -> str:
    """The line that replaces a card once it has been answered."""
    if kind == "permission":
        labels = {
            "once": "✅ تمت الموافقة مرة واحدة.",
            "always": "♾️ سمحت بهذا الإجراء دائماً.",
            "reject": "⛔ رُفض الطلب.",
        }
        base = labels.get(detail, "✅ تم الرد.")
    else:
        base = {
            "t": "✍️ أُرسلت إجابتك النصية.",
            "c": "✅ تم تأكيد اختيارك.",
            "r": "⛔ أُلغي السؤال.",
        }.get(detail, "✅ تم الرد.")
    return f"{base}\n<i>الوكيل يكمل المهمة.</i>"


PERMISSION_EVENTS = frozenset({"permission.asked", "permission.v2.asked"})
QUESTION_EVENTS = frozenset({"question.asked", "question.v2.asked"})


def parse_permission_event(event: dict[str, object]) -> PermissionCard | None:
    """Read a permission request out of a normalized agent event.

    Both server generations are accepted: the first carries ``permission`` plus
    ``patterns``, the second an ``action`` plus ``resources``. Anything that does
    not carry a request id is not a request and is left alone.
    """
    if str(event.get("type") or "") not in PERMISSION_EVENTS:
        return None
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return None
    request_id = str(properties.get("id") or "")
    if not _PERMISSION_ID.match(request_id):
        return None
    action = str(properties.get("action") or properties.get("permission") or "")
    raw_targets = properties.get("resources")
    if not isinstance(raw_targets, list):
        raw_targets = properties.get("patterns")
    targets: list[str] = []
    if isinstance(raw_targets, list):
        for item in raw_targets:
            text = _clean(item, 160)
            if text:
                targets.append(text)
    # `metadata` is deliberately never read: it can carry command arguments or
    # file contents, and this card must not become a channel for them.
    return PermissionCard(request_id=request_id, action=action, targets=tuple(targets[:8]))


def parse_question_event(event: dict[str, object]) -> tuple[str, tuple[Question, ...]] | None:
    """Read the agent's questions out of a normalized agent event."""
    if str(event.get("type") or "") not in QUESTION_EVENTS:
        return None
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return None
    request_id = str(properties.get("id") or "")
    if not _QUESTION_ID.match(request_id):
        return None
    raw_questions = properties.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        return None
    questions: list[Question] = []
    for raw in raw_questions[:3]:
        if not isinstance(raw, dict):
            continue
        options: list[tuple[str, str]] = []
        raw_options = raw.get("options")
        if isinstance(raw_options, list):
            for item in raw_options[:MAX_OPTIONS]:
                if not isinstance(item, dict):
                    continue
                label = _clean(item.get("label"), 60)
                if label:
                    options.append((label, _clean(item.get("description"), 160)))
        questions.append(
            Question(
                header=_clean(raw.get("header"), 40),
                question=_clean(raw.get("question"), MAX_TEXT_CHARS),
                options=tuple(options),
                multiple=bool(raw.get("multiple")),
                custom=bool(raw.get("custom")),
            )
        )
    if not questions:
        return None
    return request_id, tuple(questions)
