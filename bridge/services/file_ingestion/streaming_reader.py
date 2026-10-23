"""File Ingestion Engine - Streaming File Reader.

قراءة الملفات الكبيرة بتقسيمها إلى مقاطع (chunks) دون تحميلها بالكامل للذاكرة.
يدعم ملفات .md، .txt، .py، .js، وغيرها بحجم يصل إلى مئات الميجابايتات.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("opencode_bridge.file_ingestion.streaming_reader")


class StreamingFileReader:
    """قارئ الملفات المتدفق لقراءة الملفات الكبيرة بتقسيمها إلى مقاطع.

    يقرأ الملف على دفعات صغيرة (chunks) لتجنب استنفاد الذاكرة عند التعامل
    مع ملفات كبيرة (>100 MB). يدعم الاستئناف من مقطع معين ومعالجة كل مقطع
    بشكل مستقل عبر دالة رد الاستدعاء (callback).
    """

    def __init__(
        self,
        max_chunk_size: int = 64 * 1024,  # 64 KB per chunk
        overlap: int = 512,  # overlap between chunks to preserve context
        encoding: str = "utf-8",
        max_file_size: int = 500 * 1024 * 1024,  # 500 MB max
    ) -> None:
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap
        self.encoding = encoding
        self.max_file_size = max_file_size

    async def detect_encoding(self, path: Path) -> str:
        """كشف ترميز الملف تلقائياً.

        يحاول فك الترميز بـ UTF-8 أولاً، ثم UTF-16، ثم ISO-8859-1.
        """
        import chardet  # type: ignore

        try:
            with path.open("rb") as f:
                raw = f.read(65536)  # Read first 64KB for detection
            result = chardet.detect(raw)
            encoding = result.get("encoding", "utf-8")
            confidence = result.get("confidence", 0)

            if confidence and confidence < 0.5:
                log.warning(
                    "Low encoding detection confidence (%s) for %s, falling back to utf-8",
                    confidence,
                    path,
                )
                return "utf-8"
            return encoding or "utf-8"
        except Exception as exc:
            log.warning("Encoding detection failed for %s: %s, using utf-8", path, exc)
            return "utf-8"

    async def get_file_info(self, path: Path) -> dict[str, Any]:
        """الحصول على معلومات الملف دون تحميله."""
        stat = path.stat()
        encoding = await self.detect_encoding(path)
        return {
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "encoding": encoding,
            "modified": stat.st_mtime,
            "is_large": stat.st_size > self.max_chunk_size * 10,
        }

    async def stream_file(
        self,
        path: Path,
        callback: Callable[[str, int], Any],
        *,
        encoding: str | None = None,
        start_offset: int = 0,
        max_chunks: int | None = None,
    ) -> dict[str, Any]:
        """قراءة ملف بالتقسيم إلى مقاطع مع استدعاء دالة React لكل مقطع.

        Args:
            path: مسار الملف
            callback: دالة يتم استدعاؤها لكل مقطع (content, chunk_index)
            encoding: ترميز الملف (يُكتشف تلقائياً إذا لم يُحدد)
            start_offset: نقطة البدء بالبايتات
            max_chunks:最大 عدد مقاطع (للتجفيف)

        Returns:
            معلومات عن عملية القراءة (عدد المقاطع، الحجم الإجمالي، إلخ)
        """
        if encoding is None:
            encoding = await self.detect_encoding(path)

        file_size = path.stat().st_size
        if file_size > self.max_file_size:
            raise ValueError(
                f"ملف كبير جداً ({file_size / (1024*1024):.1f} MB). الحد الأقصى: "
                f"{self.max_file_size / (1024*1024):.1f} MB"
            )

        chunks_processed = 0
        total_bytes_read = 0
        offset = start_offset

        try:
            with path.open("r", encoding=encoding, errors="replace") as f:
                if start_offset > 0:
                    f.seek(start_offset)

                while True:
                    if max_chunks is not None and chunks_processed >= max_chunks:
                        break

                    chunk = f.read(self.max_chunk_size)
                    if not chunk:
                        break

                    total_bytes_read += len(chunk.encode(encoding))
                    chunks_processed += 1

                    # استدعاء دالة رد الاستدعاء لكل مقطع
                    try:
                        if inspect.iscoroutinefunction(callback):
                            await callback(chunk, chunks_processed - 1)
                        else:
                            callback(chunk, chunks_processed - 1)
                    except Exception as exc:
                        log.error("Callback failed for chunk %d: %s", chunks_processed - 1, exc)
                        raise

                    offset = f.tell()

        except Exception as exc:
            log.error("Failed to stream file %s: %s", path, exc)
            raise

        return {
            "file": str(path),
            "size": file_size,
            "size_mb": round(file_size / (1024 * 1024), 2),
            "encoding": encoding,
            "chunks_processed": chunks_processed,
            "total_bytes_read": total_bytes_read,
            "start_offset": start_offset,
            "end_offset": offset,
        }

    async def read_file_in_chunks(
        self,
        path: Path,
        *,
        encoding: str | None = None,
    ) -> AsyncIterator[tuple[str, int]]:
        """محرك تكراري لقراءة الملف مقطعاً بمقطع.

        يield كل مقطع مع مؤشره. مناسب للاستخدام في حلقات async for.
        """
        if encoding is None:
            encoding = await self.detect_encoding(path)

        chunk_index = 0
        try:
            with path.open("r", encoding=encoding, errors="replace") as f:
                while True:
                    chunk = f.read(self.max_chunk_size)
                    if not chunk:
                        break
                    yield chunk, chunk_index
                    chunk_index += 1
        except Exception as exc:
            log.error("Failed to iterate file %s: %s", path, exc)
            raise

    async def read_file_full(
        self,
        path: Path,
        *,
        encoding: str | None = None,
        max_size: int | None = None,
    ) -> str:
        """قراءة ملف كامل إلى الذاكرة (للفوات الصغيرة فقط).

        Args:
            path: مسار الملف
            encoding: ترميز الملف
            max_size: الحد الأقصى للحجم بالبايتات (للحماية من الاستنفاد)

        Returns:
            محتوى الملف كنص
        """
        if encoding is None:
            encoding = await self.detect_encoding(path)

        file_size = path.stat().st_size
        limit = max_size or self.max_chunk_size * 20  # 20 chunks max for full read

        if file_size > limit:
            raise ValueError(
                f"ملف كبير جداً للقراءة الكاملة ({file_size / (1024*1024):.1f} MB). "
                f"استخدم stream_file بدلاً من ذلك. الحد: {limit / (1024*1024):.1f} MB"
            )

        with path.open("r", encoding=encoding, errors="replace") as f:
            return f.read()
