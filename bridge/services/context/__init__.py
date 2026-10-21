"""Context & Memory Management - إدارة السياق والذاكرة.

يوفر أدوات لإدارة السياق الطويل للمهام والملفات الضخمة:
- ذاكرة المهام الطويلة (TaskMemory) مع حفظ واستعادة من القرص
- ملخص المحادثات (ConversationSummarizer) لتقليل حجم السياق
"""

from bridge.services.context.conversation_summarizer import (
    ConversationSummarizer,
    ConversationSummary,
    Message,
)
from bridge.services.context.task_memory import (
    TaskContext,
    TaskEvent,
    TaskMemory,
)

__all__ = [
    "ConversationSummarizer",
    "ConversationSummary",
    "Message",
    "TaskContext",
    "TaskEvent",
    "TaskMemory",
]
