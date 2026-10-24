"""Async, deadline-bounded host media tools for the task path.

Why this module exists: `bridge/infrastructure/media/processors.py` shells out
to pdftotext/ffprobe/ffmpeg with `subprocess.run`, which is synchronous. Calling
it directly from the event loop would freeze the entire bot — every Telegram
update, every queued task — for as long as the tool takes (up to 120s by
default). The processors are therefore never invoked in-line here:

* the blocking call runs in a worker thread (``asyncio.to_thread``), so the
  loop keeps serving updates;
* the wait is capped with ``asyncio.wait_for``, so a wedged tool cannot hold a
  task hostage;
* the tool itself carries its own shorter subprocess timeout, so a thread that
  timed out on our side still terminates instead of leaking forever;
* any failure — missing tool, non-zero exit, timeout — yields **no**
  representation. Guessing at a PDF's contents is worse than saying nothing.

Only tools already installed on the host are used. Nothing is installed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bridge.domain.attachments.intelligence import AttachmentIntelligenceRouter
from bridge.domain.attachments.media_pipeline import MediaInput, MediaPipeline
from bridge.services.attachment_context_service import (
    AttachmentContextService,
    DerivedRepresentation,
)

log = logging.getLogger("opencode_bridge.media_analysis")

DEFAULT_DEADLINE_SECONDS = 45.0
TOOL_TIMEOUT_SECONDS = 30
DEFAULT_PDF_CHUNKS = 4
_PDF_SUFFIXES = frozenset({".pdf"})


class MediaAnalysisService:
    """Extract bounded text from PDF attachments using host tools only."""

    def __init__(
        self,
        *,
        pipeline: MediaPipeline | None = None,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        tool_timeout: int = TOOL_TIMEOUT_SECONDS,
        disabled_tools: Iterable[str] = (),
    ) -> None:
        self.pipeline = pipeline or self._build_pipeline(tool_timeout, disabled_tools)
        self.deadline_seconds = max(0.1, float(deadline_seconds))
        self.router = AttachmentIntelligenceRouter()
        # Shares the caps and the honesty rules of the in-process path.
        self.limits = AttachmentContextService()

    @staticmethod
    def _build_pipeline(tool_timeout: int, disabled_tools: Iterable[str] = ()) -> MediaPipeline:
        """Reuse the existing processor set, but with a bounded tool timeout."""
        from bridge.infrastructure.media.processors import PdfProcessor
        from bridge.infrastructure.media.tools import detect_media_tools

        return MediaPipeline([
            PdfProcessor(detect_media_tools(disabled_tools), timeout=tool_timeout)
        ])

    # ---- selection ------------------------------------------------------
    @staticmethod
    def is_pdf(attachment: Any) -> bool:
        path = getattr(attachment, "path", None)
        if not path:
            return False
        mime = str(getattr(attachment, "mime", "") or "").split(";")[0].strip().lower()
        return mime == "application/pdf" or Path(str(path)).suffix.lower() in _PDF_SUFFIXES

    def pdf_representation(self, attachment: Any, analysis: Any, *, query: str) -> DerivedRepresentation | None:
        """Turn pdftotext chunks into one bounded, document-ordered excerpt."""
        chunks = [
            artifact.text
            for artifact in getattr(analysis, "artifacts", ())
            if getattr(artifact, "kind", "") == "pdf_text_chunk"
            and getattr(artifact, "text", None)
        ]
        if not chunks:
            return None
        if len(chunks) > DEFAULT_PDF_CHUNKS:
            picked = self.router.select_ordered_chunks(chunks, query, limit=DEFAULT_PDF_CHUNKS)
            body = "\n…\n".join(chunk.strip() for chunk in picked)
        else:
            body = "\n".join(chunk.strip() for chunk in chunks)
        body = body.strip()
        if not body:
            return None
        filename = str(getattr(attachment, "filename", "") or Path(str(attachment.path)).name)
        truncated = len(chunks) > DEFAULT_PDF_CHUNKS
        marker = self.limits.TRUNCATION_MARKER
        if truncated:
            budget = max(0, self.limits.MAX_PER_FILE_CHARS - len(marker))
            body = body[:budget].rstrip() + marker
        return DerivedRepresentation(filename, "pdf_text", body, truncated)

    # ---- async execution ------------------------------------------------
    async def analyze(self, path: Path, *, mime: str) -> Any | None:
        """Run the blocking pipeline in a thread under a hard deadline."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.pipeline.analyze, MediaInput(path, mime)),
                timeout=self.deadline_seconds,
            )
        except TimeoutError:
            log.warning("تجاوز استخراج الوسائط المهلة: %s", path.name)
        except Exception as exc:
            # Missing tool, non-zero exit, unreadable file: no representation.
            log.info("تعذر استخراج وسائط %s: %s", path.name, type(exc).__name__)
        return None

    async def represent_for_prompt(
        self,
        attachments: Any,
        *,
        query: str,
    ) -> tuple[str, list[DerivedRepresentation]]:
        """Return the PDF text block plus what was actually extracted."""
        derived: list[DerivedRepresentation] = []
        for attachment in list(attachments or []):
            if not self.is_pdf(attachment):
                continue
            path = Path(str(getattr(attachment, "path", "")))
            analysis = await self.analyze(
                path, mime=str(getattr(attachment, "mime", "") or "application/pdf")
            )
            if analysis is None:
                continue
            representation = self.pdf_representation(attachment, analysis, query=query)
            if representation is not None:
                derived.append(representation)
        if not derived:
            return "", []
        return self.limits.format_block(derived)
