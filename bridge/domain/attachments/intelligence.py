"""Choose bounded representations instead of forcing every attachment directly into model context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True)
class AttachmentContext:
    query: str = ""
    max_direct_bytes: int = 8 * 1024 * 1024
    max_text_chars: int = 120_000


@dataclass(frozen=True)
class AttachmentRoute:
    strategy: str
    representations: tuple[str, ...]
    reason: str


class AttachmentIntelligenceRouter:
    DIRECT_MIME_PREFIXES: ClassVar[tuple[str, ...]] = ("image/", "text/")
    DIRECT_MIMES: ClassVar[frozenset[str]] = frozenset({"application/pdf"})

    def route(self, *, path: Path, mime: str, size: int, context: AttachmentContext) -> AttachmentRoute:
        if size <= context.max_direct_bytes and (mime.startswith(self.DIRECT_MIME_PREFIXES) or mime in self.DIRECT_MIMES):
            return AttachmentRoute("direct", ("attachment",), "small supported attachment")
        if mime.startswith("text/"):
            return AttachmentRoute("text_index", ("chunks", "index", "selected_chunks"), "large text requires selective context")
        if mime == "application/pdf":
            return AttachmentRoute("pdf", ("text", "pages", "images", "selected_chunks"), "PDF decomposed for context")
        if mime.startswith("video/"):
            return AttachmentRoute("video", ("frames", "transcript", "metadata"), "video represented by selected frames/transcript")
        if mime.startswith("audio/"):
            return AttachmentRoute("audio", ("transcript", "timestamps", "metadata"), "audio represented by transcript")
        if mime in {"application/zip", "application/x-zip-compressed"} or path.suffix.lower() == ".zip":
            return AttachmentRoute("archive", ("manifest", "selected_files"), "archive listed before selective extraction")
        return AttachmentRoute("binary", ("metadata",), "unsupported binary kept out of direct context")

    @staticmethod
    def select_ordered_chunks(chunks: list[str], query: str, *, limit: int = 4) -> list[str]:
        """Relevance-selected chunks returned in **document order**.

        ``select_text_chunks`` ranks strictly by score, which is right for a
        search-result list and wrong for a prompt excerpt: a model reading
        "part 7, part 3, part 9" loses the document's own sequence and can draw
        the wrong conclusion about what precedes what. This variant keeps the
        same selection and restores the original order, falling back to the
        leading chunks when nothing matches.
        """
        if limit < 1 or not chunks:
            return []
        terms = {term.casefold() for term in query.split() if len(term) > 2}
        if not terms:
            return chunks[:limit]
        ranked: list[tuple[int, int, int]] = []
        for index, chunk in enumerate(chunks):
            folded = chunk.casefold()
            score = sum(folded.count(term) for term in terms)
            if score > 0:
                ranked.append((score, -index, index))
        if not ranked:
            return chunks[:limit]
        ranked.sort(reverse=True)
        # Sort by document position only; re-sorting the tuples would order by
        # score ascending and silently reverse equal-scoring neighbours.
        return [chunks[index] for index in sorted(index for _, _, index in ranked[:limit])]

    @staticmethod
    def select_text_chunks(chunks: list[str], query: str, *, limit: int = 6) -> list[str]:
        terms = {term.casefold() for term in query.split() if len(term) > 2}
        if not terms:
            return chunks[:limit]
        scored = []
        for index, chunk in enumerate(chunks):
            folded = chunk.casefold()
            score = sum(folded.count(term) for term in terms)
            scored.append((score, -index, chunk))
        scored.sort(reverse=True)
        selected = [chunk for score, _, chunk in scored if score > 0][:limit]
        return selected or chunks[:limit]
