"""Long-form professional Telegram delivery with images and chunking."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from telegram import InputMediaPhoto, InputFile

log = logging.getLogger("opencode_bridge.telegram.long_text")


class LongTextDelivery:
    """Deliver very long agent outputs or news texts with images and pagination."""

    def __init__(self, bot: Any, max_message: int = 4000) -> None:
        self.bot = bot
        self.max_message = max_message

    async def send_long_text(
        self,
        chat_id: int,
        text: str,
        images: list[str | Path] | None = None,
        caption_first: bool = False,
    ) -> list[int] | None:
        from bridge.telegram.rendering.agent_output import chunk_for_telegram
        chunks = chunk_for_telegram(text, chunk_size=self.max_message - 200)
        message_ids: list[int] = []
        for idx, chunk in enumerate(chunks[:5]):  # Limit to 5 chunks to avoid flooding
            if idx == 0 and images and caption_first:
                # First chunk with first image as caption
                media = InputMediaPhoto(media=InputFile(str(images[0])), caption=chunk[:1024])
                msg = await self.bot.send_media_group(chat_id=chat_id, media=[media])
                message_ids.append(msg[0].message_id if msg else 0)
            else:
                msg = await self.bot.send_message(chat_id=chat_id, text=chunk)
                message_ids.append(msg.message_id)
        # Send remaining images separately if any
        if images and len(images) > 1:
            for img_path in images[1:4]:
                try:
                    msg = await self.bot.send_photo(
                        chat_id=chat_id, photo=InputFile(str(img_path)), caption="📎 صورة إضافية"
                    )
                    message_ids.append(msg.message_id)
                except Exception:
                    log.warning("failed to send image %s", img_path)
        return message_ids if message_ids else None
