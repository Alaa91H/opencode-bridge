"""Markdown Processor - استخراج وتنظيم محتوى ملفات markdown الضخمة.

يعالج ملفات markdown لاستخراج العناوين، الفقرات، بلوكات الكود، الجداول،
والروابط بشكل هيكلي يسهل معالجته من قبل الوكيل.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("opencode_bridge.file_ingestion.markdown_processor")


@dataclass
class CodeBlock:
    """بلوك كود مستخرج من ملف markdown."""

    language: str
    content: str
    start_line: int
    end_line: int


@dataclass
class LinkReference:
    """مرجع رابط مستخرج من ملف markdown."""

    text: str
    url: str
    line: int


@dataclass
class DocumentSection:
    """قسم من مستند markdown مع محتواه وبياناته الوصفية."""

    title: str
    level: int  # 1-6 for h1-h6
    content: str
    start_line: int
    end_line: int
    subsections: list[DocumentSection] = field(default_factory=list)
    code_blocks: list[CodeBlock] = field(default_factory=list)
    links: list[LinkReference] = field(default_factory=list)
    word_count: int = 0
    char_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.content.strip() and not self.subsections


class MarkdownProcessor:
    """معالج ملفات markdown المتقدم."""

    def __init__(self, max_section_size: int = 32 * 1024) -> None:
        self.max_section_size = max_section_size

        # أنماط التعبير المنظم للعناوين، الكود، والروابط
        self._heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        self._code_block_pattern = re.compile(
            r"^```(\w*)\n(.*?)\n```$", re.MULTILINE | re.DOTALL
        )
        self._inline_code_pattern = re.compile(r"`([^`]+)`")
        self._link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
        self._table_pattern = re.compile(r"^\|.*\|$", re.MULTILINE)

    async def extract_sections(self, content: str) -> list[DocumentSection]:
        """استخراج الأقسام من محتوى markdown بناءً على العناوين.

        يقسم المستند إلى أقسام بناءً على العناوين (h1-h6)، مع الحفاظ على
        التسلسل الهرمي بين الأقسام الفرعية.
        """
        lines = content.split("\n")
        sections: list[DocumentSection] = []
        section_stack: list[tuple[int, DocumentSection]] = []  # (level, section)

        current_content: list[str] = []
        current_title = ""
        current_level = 0
        current_start_line = 0

        def flush_current_section() -> None:
            nonlocal current_content, current_title, current_level, current_start_line
            if current_title or current_content:
                section_content = "\n".join(current_content).strip()
                section = DocumentSection(
                    title=current_title,
                    level=current_level,
                    content=section_content,
                    start_line=current_start_line,
                    end_line=current_start_line + len(current_content) - 1,
                    word_count=len(section_content.split()),
                    char_count=len(section_content),
                )

                # استخراج بلوكات الكود والروابط من القسم
                section.code_blocks = self.extract_code_blocks(section_content)
                section.links = self.extract_links(section_content)

                # إضافة القسم إلى التسلسل الهرمي
                while section_stack and section_stack[-1][0] >= current_level:
                    section_stack.pop()
                if section_stack:
                    section_stack[-1][1].subsections.append(section)
                else:
                    sections.append(section)

                section_stack.append((current_level, section))

            current_content = []
            current_title = ""
            current_level = 0
            current_start_line = 0

        for i, line in enumerate(lines):
            heading_match = self._heading_pattern.match(line)
            if heading_match:
                #.flush القسم الحالي قبل البدء بقسم جديد
                if current_title or current_content:
                    flush_current_section()

                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                current_title = title
                current_level = level
                current_start_line = i + 1
                current_content = []
            else:
                current_content.append(line)

        #.flush القسم الأخير
        if current_title or current_content:
            flush_current_section()

        return sections

    def extract_code_blocks(self, content: str) -> list[CodeBlock]:
        """استخراج بلوكات الكود من محتوى markdown مع تحديد لغة البرمجة."""
        blocks: list[CodeBlock] = []
        lines = content.split("\n")

        i = 0
        while i < len(lines):
            line = lines[i]
            # البحث عن بداية بلوك كود
            code_start = re.match(r"^```(\w*)", line)
            if code_start:
                language = code_start.group(1) or "text"
                start_line = i + 1
                content_lines: list[str] = []
                i += 1

                # جمع سطور الكود حتى نهاية البلوك
                while i < len(lines) and not lines[i].startswith("```"):
                    content_lines.append(lines[i])
                    i += 1

                end_line = i + 1
                if i < len(lines):
                    i += 1  # تجاوز السطر الذي يحتوي على ```

                blocks.append(
                    CodeBlock(
                        language=language,
                        content="\n".join(content_lines),
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
            else:
                i += 1

        return blocks

    def extract_links(self, content: str) -> list[LinkReference]:
        """استخراج الروابط من محتوى markdown."""
        links: list[LinkReference] = []
        lines = content.split("\n")

        for line_num, line in enumerate(lines, 1):
            for match in self._link_pattern.finditer(line):
                links.append(
                    LinkReference(
                        text=match.group(1),
                        url=match.group(2),
                        line=line_num,
                    )
                )

        return links

    def extract_inline_code(self, content: str) -> list[str]:
        """استخراج الكود المضمن من contenido."""
        return self._inline_code_pattern.findall(content)

    async def summarize_section(
        self,
        section: DocumentSection,
        max_tokens: int = 2000,
        model_manager: Any = None,
    ) -> str:
        """تلخيص قسم طويل باستخدام الوكيل إذا لزم الأمر.

        إذا كان القسم قصيراً بما يكفي، يُعاد كما هو. إذا كان طويلاً،
        يتم استخدام الوكيل لإنشاء تلخيص موجز يحتفظ بالمعلومات المهمة.
        """
        estimated_tokens = len(section.content) // 4  # تقدير تقريبي

        if estimated_tokens <= max_tokens:
            return section.content

        if model_manager is None:
            # إذا لم يتوفر مدير النماذج، نقطع المحتوى مع إضافة ملاحظة
            truncated = section.content[: max_tokens * 4]
            return (
                f"{truncated}\n\n... [محتوى مقطوع - يتجاوز الحد المسموح: "
                f"{estimated_tokens} توكن، الحد: {max_tokens} توكن]"
            )

        # استخدام الوكيل للتلخيص (يتم تنفيذه عند توفر نموذج مناسب)
        try:
            # محاولة استخدام نموذج التلخيص عبر العميل المتاح
            if hasattr(model_manager, "best_available"):
                best_model = await model_manager.best_available()
                if best_model:
                    # هذا سيتطلب تنفيذ في العميل الخاص بالوكيل
                    pass
        except Exception as exc:
            log.warning("فشل في تلخيص القسم عبر الوكيل: %s", exc)

        # fallback: قطع المحتوى
        truncated = section.content[: max_tokens * 4]
        return (
            f"{truncated}\n\n... [محتوى مقطوع - يتجاوز الحد المسموح: "
            f"{estimated_tokens} توكن، الحد: {max_tokens} توكن]"
        )

    async def process_markdown_file(
        self,
        content: str,
        file_name: str = "document.md",
    ) -> dict[str, Any]:
        """معالجة ملف markdown بالكامل واستخراج جميع مكوناته.

        Returns:
            قاموس يحتوي على الأقسام، بلوكات الكود، الروابط، والإحصائيات.
        """
        sections = await self.extract_sections(content)
        all_code_blocks: list[CodeBlock] = []
        all_links: list[LinkReference] = []

        for section in sections:
            all_code_blocks.extend(section.code_blocks)
            all_links.extend(section.links)
            # جمع الفرعيات أيضاً
            for subsection in section.subsections:
                all_code_blocks.extend(subsection.code_blocks)
                all_links.extend(subsection.links)

        total_words = sum(s.word_count for s in sections)
        total_chars = sum(s.char_count for s in sections)

        return {
            "file_name": file_name,
            "total_sections": len(sections),
            "total_code_blocks": len(all_code_blocks),
            "total_links": len(all_links),
            "total_words": total_words,
            "total_chars": total_chars,
            "sections": sections,
            "code_blocks": all_code_blocks,
            "links": all_links,
            "estimated_reading_time_minutes": max(1, total_words // 200),
        }
