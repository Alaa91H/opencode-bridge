"""Safe attachment storage and output collection for the Telegram bridge."""

from __future__ import annotations

import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_OUTGOING_FILES = 10


class AttachmentError(ValueError):
    """Raised when an attachment cannot be accepted safely."""


@dataclass(frozen=True)
class StoredAttachment:
    path: str
    filename: str
    mime: str
    size: int
    kind: str

    def to_message_part(self) -> dict[str, str]:
        return {
            "type": "file",
            "url": Path(self.path).resolve().as_uri(),
            "mime": self.mime,
            "filename": self.filename,
        }

    def to_record(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "filename": self.filename,
            "mime": self.mime,
            "size": self.size,
            "kind": self.kind,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "StoredAttachment":
        required = ("path", "filename", "mime", "size", "kind")
        if not all(key in record for key in required):
            raise AttachmentError("بيانات المرفق غير مكتملة")
        return cls(
            path=str(record["path"]),
            filename=str(record["filename"]),
            mime=str(record["mime"]),
            size=int(record["size"]),
            kind=str(record["kind"]),
        )


def _safe_filename(value: str, fallback: str) -> str:
    candidate = Path(value or fallback).name.strip()
    candidate = SAFE_FILENAME_RE.sub("_", candidate).strip("._")
    if not candidate:
        candidate = fallback
    return candidate[:140]


def _mime_and_extension(value: str | None, fallback: str) -> tuple[str, str]:
    mime = (value or "").strip().lower()
    if not mime:
        guessed, _ = mimetypes.guess_type(fallback)
        mime = guessed or "application/octet-stream"
    extension = mimetypes.guess_extension(mime) or ""
    return mime, extension


def select_telegram_attachment(message: Any) -> tuple[Any, str, str, str]:
    """Return Telegram media object, kind, proposed filename, and MIME type."""
    if getattr(message, "document", None):
        media = message.document
        mime, extension = _mime_and_extension(getattr(media, "mime_type", None), getattr(media, "file_name", "document"))
        return media, "document", _safe_filename(getattr(media, "file_name", ""), f"document{extension}"), mime
    if getattr(message, "photo", None):
        media = message.photo[-1]
        return media, "photo", f"photo_{getattr(media, 'file_unique_id', uuid.uuid4().hex)}.jpg", "image/jpeg"
    if getattr(message, "video", None):
        media = message.video
        mime, extension = _mime_and_extension(getattr(media, "mime_type", None), getattr(media, "file_name", "video"))
        return media, "video", _safe_filename(getattr(media, "file_name", ""), f"video{extension or '.mp4'}"), mime
    if getattr(message, "audio", None):
        media = message.audio
        mime, extension = _mime_and_extension(getattr(media, "mime_type", None), getattr(media, "file_name", "audio"))
        return media, "audio", _safe_filename(getattr(media, "file_name", ""), f"audio{extension}"), mime
    if getattr(message, "voice", None):
        media = message.voice
        mime, extension = _mime_and_extension(getattr(media, "mime_type", None), "voice")
        return media, "voice", f"voice_{getattr(media, 'file_unique_id', uuid.uuid4().hex)}{extension or '.ogg'}", mime
    if getattr(message, "animation", None):
        media = message.animation
        mime, extension = _mime_and_extension(getattr(media, "mime_type", None), getattr(media, "file_name", "animation"))
        return media, "animation", _safe_filename(getattr(media, "file_name", ""), f"animation{extension or '.mp4'}"), mime
    if getattr(message, "video_note", None):
        media = message.video_note
        return media, "video_note", f"video_note_{getattr(media, 'file_unique_id', uuid.uuid4().hex)}.mp4", "video/mp4"
    if getattr(message, "sticker", None):
        media = message.sticker
        if getattr(media, "is_video", False):
            extension, mime = ".webm", "video/webm"
        elif getattr(media, "is_animated", False):
            extension, mime = ".tgs", "application/x-tgsticker"
        else:
            extension, mime = ".webp", "image/webp"
        return media, "sticker", f"sticker_{getattr(media, 'file_unique_id', uuid.uuid4().hex)}{extension}", mime
    raise AttachmentError("نوع المرفق غير مدعوم")


class AttachmentStore:
    """Stores Telegram uploads and exposes only managed task output files."""

    def __init__(self, root: Path, max_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES) -> None:
        self.root = root.resolve()
        self.incoming_root = self.root / "incoming"
        self.outgoing_root = self.root / "outgoing"
        self.max_bytes = max_bytes

    def ensure_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.root.chmod(0o750)
        for directory in (self.incoming_root, self.outgoing_root):
            directory.mkdir(parents=True, exist_ok=True, mode=0o750)
            directory.chmod(0o750)

    def incoming_directory(self, owner_id: str) -> Path:
        self.ensure_directories()
        directory = self.incoming_root / str(owner_id) / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False, mode=0o750)
        return directory

    def task_output_directory(self, task_id: int) -> Path:
        self.ensure_directories()
        directory = self.outgoing_root / f"task-{task_id}"
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        return directory

    async def download_from_message(self, message: Any, bot: Any, owner_id: str) -> StoredAttachment:
        media, kind, proposed_name, mime = select_telegram_attachment(message)
        declared_size = int(getattr(media, "file_size", 0) or 0)
        if declared_size > self.max_bytes:
            raise AttachmentError(f"حجم المرفق أكبر من الحد المسموح ({self.max_bytes // (1024 * 1024)} MiB)")
        file_id = getattr(media, "file_id", None)
        if not file_id:
            raise AttachmentError("لم يقدّم تيليغرام معرّفًا صالحًا للمرفق")
        destination = self.incoming_directory(str(owner_id)) / _safe_filename(proposed_name, "attachment.bin")
        remote_file = await bot.get_file(file_id)
        await remote_file.download_to_drive(custom_path=destination)
        actual_size = destination.stat().st_size
        if actual_size > self.max_bytes:
            destination.unlink(missing_ok=True)
            raise AttachmentError(f"حجم المرفق بعد التنزيل أكبر من الحد المسموح ({self.max_bytes // (1024 * 1024)} MiB)")
        destination.chmod(0o640)
        return StoredAttachment(
            path=str(destination.resolve()),
            filename=destination.name,
            mime=mime,
            size=actual_size,
            kind=kind,
        )

    def validate_input_records(self, records: Iterable[dict[str, Any]]) -> list[StoredAttachment]:
        attachments: list[StoredAttachment] = []
        for record in records:
            attachment = StoredAttachment.from_record(record)
            path = Path(attachment.path).resolve()
            if not path.is_file() or path.is_symlink() or not path.is_relative_to(self.incoming_root):
                raise AttachmentError("مسار أحد المرفقات لم يعد صالحًا")
            if path.stat().st_size > self.max_bytes:
                raise AttachmentError("أحد المرفقات يتجاوز حد الحجم المسموح")
            attachments.append(attachment)
        return attachments

    def collect_task_outputs(self, task_id: int, max_files: int = DEFAULT_MAX_OUTGOING_FILES) -> list[Path]:
        output_directory = self.task_output_directory(task_id).resolve()
        files: list[Path] = []
        for candidate in sorted(output_directory.rglob("*")):
            if len(files) >= max_files:
                break
            if not candidate.is_file() or candidate.is_symlink():
                continue
            path = candidate.resolve()
            if not path.is_relative_to(output_directory) or path.stat().st_size > self.max_bytes:
                continue
            files.append(path)
        return files


def attachment_prompt_note(attachments: Iterable[StoredAttachment], output_directory: Path) -> str:
    entries = [f"- {item.filename} ({item.mime}, {item.size} bytes): {item.path}" for item in attachments]
    attachment_context = (
        "المرفقات التالية وصلت من مستخدم تيليغرام مُصرّح. استخدمها فقط لتنفيذ الطلب، ولا تنسخها خارج مساحة البوت.\n"
        + "\n".join(entries)
        if entries
        else "لا توجد مرفقات واردة مع هذه المهمة."
    )
    return (
        attachment_context
        + "\n\nإذا أنشأت ملفًا يريد المستخدم استلامه، فاكتبه فقط داخل هذا المجلد: "
        + str(output_directory)
        + "\nلا ترسل أي ملف من مسار آخر، واذكر في ردك النصي باختصار ما أنشأته."
    )
