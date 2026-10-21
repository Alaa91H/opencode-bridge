"""Long Context Manager - إدارة السياق الطويل للملفات الضخمة.

يقسم السياق الطويل إلى أجزاء ذكية تناسب حدود النموذج، مع الحفاظ على
الأجزاء الأكثر صلة بالاستعلام وضغط الأجزاء الأقل أهمية.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bridge.services.file_ingestion.markdown_processor import (
    DocumentSection,
    MarkdownProcessor,
)

log = logging.getLogger("opencode_bridge.file_ingestion.context_manager")


@dataclass
class ContextBudget:
    """ميزانية السياق للنموذج."""

    total_tokens: int
    reserved_for_output: int = 4000
    reserved_for_system: int = 2000
    reserved_for_history: int = 4000

    @property
    def available_for_content(self) -> int:
        return max(
            0,
            self.total_tokens - self.reserved_for_output
            - self.reserved_for_system
            - self.reserved_for_history,
        )


@dataclass
class ContextSegment:
    """قطعة من السياق مع أولوية وأهمية."""

    content: str
    token_count: int
    priority: int  # 1 = highest, 5 = lowest
    source: str  # file name, section title, etc.
    relevance_score: float = 0.0  # 0.0 to 1.0

    def fits_in_budget(self, remaining: int) -> bool:
        return self.token_count <= remaining


@dataclass
class CompressedContext:
    """السياق المضغوط الجاهز للإرسال إلى النموذج."""

    content: str
    total_tokens: int
    segments_included: int
    segments_excluded: int
    compression_ratio: float
    sources: list[str] = field(default_factory=list)


class LongContextManager:
    """مدير السياق الطويل للملفات الضخمة.

    يقسم السياق إلى أجزاء، يصنفها بالأهمية، ويختار الأجزاء الأكثر صلة
    بالاستعلام لتناسب ميزانية السياق المتاحة.
    """

    MAX_CONTEXT_TOKENS = 128000  # الحد الأقصى لمعظم النماذج الحديثة

    def __init__(
        self,
        max_context_tokens: int = MAX_CONTEXT_TOKENS,
        max_section_size: int = 32 * 1024,
    ) -> None:
        self.max_context_tokens = max_context_tokens
        self.max_section_size = max_section_size
        self.markdown_processor = MarkdownProcessor(
            max_section_size=max_section_size
        )

    def estimate_tokens(self, text: str) -> int:
        """تقدير عدد التوكنات في النص.

        تقدير تقريبي: 1 توكن ≈ 4 أحرف للغة الإنجليزية، وحوالي 2-3 أحرف
        للعربية. نستخدم متوسطاً省حياً.
        """
        # تقديرات متوسطة: العربية تحتاج حوالي 2.5 حرف لكل توكن
        arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
        other_chars = len(text) - arabic_chars
        estimated = arabic_chars // 2.5 + other_chars // 4
        return max(1, int(estimated))

    async def compress_sections(
        self,
        sections: list[DocumentSection],
        query: str | None = None,
        budget: int | None = None,
    ) -> CompressedContext:
        """ضغط قائمة الأقسام إلى سياق يناسب الميزانية المتاحة.

        إذا كان هناك استعلام (query)، يتم اختيار الأقسام الأكثر صلة به.
        إذا لم يوجد استعلام، يتم اختيار الأقسام بناءً على الأولوية والترتيب.
        """
        if budget is None:
            budget = self.max_context_tokens

        # حساب توكنات كل قسم
        segments: list[ContextSegment] = []
        for section in sections:
            tokens = self.estimate_tokens(section.content)
            segments.append(
                ContextSegment(
                    content=section.content,
                    token_count=tokens,
                    priority=section.level,  # h1 = highest priority (1)
                    source=section.title,
                    relevance_score=0.0,
                )
            )

        # إذا كان هناك استعلام، حساب نقاط الصلة
        if query:
            segments = await self._rank_by_relevance(segments, query)
        else:
            # ترتيب حسب الأولوية ثم الحجم (الأصغر أولاً للاستفادة القصوى)
            segments.sort(key=lambda s: (s.priority, s.token_count))

        # اختيار الأقسام التي تناسب الميزانية
        included: list[ContextSegment] = []
        excluded: list[ContextSegment] = []
        remaining = budget

        for segment in segments:
            if segment.fits_in_budget(remaining):
                included.append(segment)
                remaining -= segment.token_count
            else:
                excluded.append(segment)

        # بناء السياق النهائي
        included.sort(key=lambda s: s.priority)
        context_parts = [seg.content for seg in included]
        full_content = "\n\n---\n\n".join(context_parts)

        sources = list({seg.source for seg in included if seg.source})

        total_tokens = sum(seg.token_count for seg in included)

        return CompressedContext(
            content=full_content,
            total_tokens=total_tokens,
            segments_included=len(included),
            segments_excluded=len(excluded),
            compression_ratio=round(
                total_tokens / max(1, sum(s.token_count for s in segments)), 2
            ),
            sources=sources,
        )

    async def _rank_by_relevance(
        self,
        segments: list[ContextSegment],
        query: str,
    ) -> list[ContextSegment]:
        """تصنيف الأقسام بناءً على صفتها بالاستعلام باستخدام تشابه بسيط.

        في الإصدار المستقبلي، يمكن استبدال هذا بنماذج التضمين (embeddings)
        للدقة الأعلى.
        """
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for segment in segments:
            content_lower = segment.content.lower()
            content_words = set(content_lower.split())

            # حساب التداخل بين كلمات الاستعلام وكلمات المحتوى
            overlap = len(query_words & content_words)
            total_query_words = len(query_words)

            if total_query_words > 0:
                segment.relevance_score = overlap / total_query_words
            else:
                segment.relevance_score = 0.0

            # زيادة النقاط إذا كان الاستعلام يظهر في العنوان
            if any(word in segment.source.lower() for word in query_words):
                segment.relevance_score += 0.2

            # 제한 النطاق إلى 1.0
            segment.relevance_score = min(1.0, segment.relevance_score)

        # ترتيب حسب الصلة (الأعلى أولاً)، ثم الأولوية
        segments.sort(key=lambda s: (-s.relevance_score, s.priority))
        return segments

    async def process_file_for_context(
        self,
        file_path: str,
        file_content: str,
        query: str | None = None,
        budget: int | None = None,
        file_name: str | None = None,
    ) -> CompressedContext:
        """معالجة ملف كامل وتحويله إلى سياق مضغوط جاهز للنموذج.

        هذه هي الدالة الرئيسية التي تربط بين قارئ الملفات، معالج
        الماركداون، ومدير السياق.
        """
        if file_name is None:
            file_name = file_path.split("/")[-1]

        # معالجة الملف كماركداون لاستخراج الأقسام
        processing_result = await self.markdown_processor.process_markdown_file(
            content=file_content,
            file_name=file_name,
        )

        sections = processing_result["sections"]

        # ضغط الأقسام إلى سياق مناسب
        compressed = await self.compress_sections(
            sections=sections,
            query=query,
            budget=budget,
        )

        return compressed

    async def select_relevant_sections(
        self,
        sections: list[DocumentSection],
        query: str,
        max_sections: int = 10,
    ) -> list[DocumentSection]:
        """اختيار الأقسام الأكثر صلة بالاستعلام دون ضغط."""
        segments: list[ContextSegment] = []
        for section in sections:
            tokens = self.estimate_tokens(section.content)
            segments.append(
                ContextSegment(
                    content=section.content,
                    token_count=tokens,
                    priority=section.level,
                    source=section.title,
                    relevance_score=0.0,
                )
            )

        ranked = await self._rank_by_relevance(segments, query)
        top_segments = ranked[:max_sections]

        # إرجاع الأقسام الأصلية المقابلة
        section_map = {s.title: s for s in sections}
        result = []
        for seg in top_segments:
            if seg.source in section_map:
                result.append(section_map[seg.source])

        return result

    def get_budget_for_task(
        self,
        task_type: str = "general",
        has_code: bool = False,
        has_long_output: bool = False,
    ) -> ContextBudget:
        """الحصول على ميزانية السياق المناسبة لنوع المهمة."""
        base = self.max_context_tokens

        # تقليل الميزانية إذا كان هناك كود (يحتاج مساحة أكبر للإخراج)
        if has_code:
            base = int(base * 0.8)

        # تقليل الميزانية إذا كان الإخراج طويلاً
        if has_long_output:
            base = int(base * 0.7)

        return ContextBudget(total_tokens=base)
