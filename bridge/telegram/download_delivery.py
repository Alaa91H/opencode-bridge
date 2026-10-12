"""Resend managed downloads without exceeding Telegram's file-size limit."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from bridge.services.download_service import DownloadError, DownloadRecord

MAX_PARTS = 60
SAFE_BOT_LIMIT = 49 * 1024 * 1024


async def deliver_download(bot: Any, chat_id: int, record: DownloadRecord) -> int:
    source = Path(record.path)
    if source.is_symlink() or not source.is_file() or source.stat().st_size != record.size_bytes:
        raise DownloadError("Stored download is missing or has changed")
    chunk_size = min(SAFE_BOT_LIMIT, int(record.direct_send_limit))
    if chunk_size <= 0:
        raise DownloadError("Invalid Telegram upload limit")
    count = (record.size_bytes + chunk_size - 1) // chunk_size
    if count > MAX_PARTS:
        raise DownloadError("File requires too many Telegram uploads")
    if count == 1:
        with source.open("rb") as stream:
            await bot.send_document(chat_id=chat_id, document=stream,
                                    filename=record.filename)
        return 1
    await bot.send_message(chat_id=chat_id, text=(
        f"Sending {record.filename} in {count} ordered parts; combine them "
        "in order after downloading. / إرسال الملف على أجزاء مرقمة."
    ))
    with source.open("rb") as original:
        for index in range(count):
            remaining = chunk_size
            fd, temporary = tempfile.mkstemp(prefix=".telegram-part-", dir=source.parent)
            try:
                with os.fdopen(fd, "wb") as part:
                    while remaining:
                        data = original.read(min(1024 * 1024, remaining))
                        if not data:
                            break
                        part.write(data)
                        remaining -= len(data)
                with open(temporary, "rb") as stream:
                    await bot.send_document(
                        chat_id=chat_id, document=stream,
                        filename=f"{record.filename}.part{index + 1:03d}")
            finally:
                Path(temporary).unlink(missing_ok=True)
    return count
