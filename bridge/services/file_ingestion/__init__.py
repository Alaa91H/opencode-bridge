"""File Ingestion Engine - نظام قراءة وتنفيذ الملفات الضخمة.

يوفر هذا المجلد أدوات لقراءة ومعالجة الملفات الضخمة (خاصة .md) دون
استنفاد الذاكرة، مع دعم التقسيم إلى مقاطع، استخراج العناوين والكود،
والروابط، وإدارة السياق الطويل للنماذج.
"""

from bridge.services.file_ingestion.context_manager import (
    CompressedContext,
    ContextBudget,
    ContextSegment,
    LongContextManager,
)
from bridge.services.file_ingestion.markdown_processor import (
    CodeBlock,
    DocumentSection,
    LinkReference,
    MarkdownProcessor,
)
from bridge.services.file_ingestion.streaming_reader import StreamingFileReader

__all__ = [
    "CodeBlock",
    "CompressedContext",
    "ContextBudget",
    "ContextSegment",
    "DocumentSection",
    "LinkReference",
    "LongContextManager",
    "MarkdownProcessor",
    "StreamingFileReader",
]
