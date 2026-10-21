"""Conversation Summarizer - ملخص المحادثات.

يطيل المحادثات الطويلة للحفاظ على السياق دون تجاوز حدود النموذج،
مما يسمح بالاستمرار في المحادثات الطويلة دون فقدان المعلومات المهمة.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("opencode_bridge.context.conversation_summarizer")


@dataclass
class Message:
    """رسالة واحدة في المحادثة."""

    id: str
    role: str  # user, assistant, system
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationSummary:
    """ملخص محادثة."""

    id: str
    conversation_id: str
    summary: str
    key_points: list[str]
    decisions: list[str]
    action_items: list[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    message_count: int = 0
    token_count: int = 0
    source_message_ids: list[str] = field(default_factory=list)


class ConversationSummarizer:
    """ملخص المحادثات الطويلة.

    يلخص المحادثات الطويلة للحفاظ على السياق دون تجاوز حدود النموذج،
    مع الحفاظ على النقاط الهامة، القرارات، والمهام المطلوبة.
    """

    def __init__(
        self,
        max_summary_length: int = 2000,
        max_messages_per_summary: int = 50,
    ) -> None:
        self.max_summary_length = max_summary_length
        self.max_messages_per_summary = max_messages_per_summary
        self._summaries: dict[str, ConversationSummary] = {}

    def estimate_tokens(self, text: str) -> int:
        """تقدير عدد التوكنات في النص."""
        # تقدير تقريبي: العربية تحتاج حوالي 2.5 حرف لكل توكن
        arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
        other_chars = len(text) - arabic_chars
        estimated = arabic_chars // 2.5 + other_chars // 4
        return max(1, int(estimated))

    def summarize_messages(
        self,
        messages: list[Message],
        conversation_id: str,
        summary_id: str | None = None,
    ) -> ConversationSummary:
        """تلخيص قائمة رسائل إلى ملخص مضغوط.

        Args:
            messages: قائمة الرسائل للتلخيص
            conversation_id: معرف المحادثة
            summary_id: معرف الملخص (يُولد تلقائياً إذا لم يُحدد)

        Returns:
            ConversationSummary مع الملخص والنقاط الرئيسية
        """
        if summary_id is None:
            import uuid

            summary_id = str(uuid.uuid4())

        if not messages:
            return ConversationSummary(
                id=summary_id,
                conversation_id=conversation_id,
                summary="محادثة فارغة",
                key_points=[],
                decisions=[],
                action_items=[],
                message_count=0,
                token_count=0,
            )

        # استخراج النصوص من الرسائل
        all_text = "\n".join(
            f"[{m.role}] {m.content}" for m in messages
        )

        # تقدير التوكنات
        total_tokens = self.estimate_tokens(all_text)

        # استخراج النقاط الرئيسية (بسيط - بناءً على الكلمات الرئيسية)
        key_points = self._extract_key_points(messages)

        # استخراج القرارات
        decisions = self._extract_decisions(messages)

        # استخراج المهام المطلوبة
        action_items = self._extract_action_items(messages)

        # إنشاء الملخص النصي
        summary_text = self._generate_summary_text(
            messages, key_points, decisions, action_items
        )

        # التأكد من أن الملخص لا يتجاوز الطول الأقصى
        if len(summary_text) > self.max_summary_length * 4:  # تقريبي بالتوكنات
            summary_text = summary_text[: self.max_summary_length * 4] + "\n... [مقطع]"

        summary = ConversationSummary(
            id=summary_id,
            conversation_id=conversation_id,
            summary=summary_text,
            key_points=key_points,
            decisions=decisions,
            action_items=action_items,
            created_at=datetime.now(UTC),
            message_count=len(messages),
            token_count=total_tokens,
            source_message_ids=[m.id for m in messages],
        )

        self._summaries[summary_id] = summary
        log.info(
            "تم إنشاء ملخص محادثة %s (%d رسائل، %d توكن)",
            summary_id,
            len(messages),
            total_tokens,
        )
        return summary

    def _extract_key_points(self, messages: list[Message]) -> list[str]:
        """استخراج النقاط الرئيسية من الرسائل."""
        key_points: list[str] = []
        keywords = [
            "مهم", "重要", "重要", "main", "primary", "key", "أساسي",
            "هام", "essential", "critical", "ضروري", "رئيسي",
        ]

        for message in messages:
            content_lower = message.content.lower()
            for keyword in keywords:
                if keyword in content_lower:
                    # استخراج الجملة التي تحتوي على الكلمة المفتاحية
                    sentences = message.content.split("。")
                    sentences.extend(message.content.split(". "))
                    for sentence in sentences:
                        if keyword in sentence.lower() and len(sentence) > 10:
                            key_points.append(sentence.strip()[:100])
                            break

        # إزالة التكرارات مع الحفاظ على الترتيب
        seen: set[str] = set()
        unique_points: list[str] = []
        for point in key_points:
            if point not in seen:
                seen.add(point)
                unique_points.append(point)

        return unique_points[:10]

    def _extract_decisions(self, messages: list[Message]) -> list[str]:
        """استخراج القرارات من الرسائل."""
        decisions: list[str] = []
        decision_indicators = [
            "تم اتخاذ قرار", "decision", "قرر", "قررنا", "اتفقنا",
            "agreed", "decided", "سنتابع", "will proceed",
        ]

        for message in messages:
            content_lower = message.content.lower()
            for indicator in decision_indicators:
                if indicator in content_lower:
                    # استخراج الجملة التي تحتوي على مؤشر القرار
                    sentences = message.content.split("。")
                    sentences.extend(message.content.split(". "))
                    for sentence in sentences:
                        if indicator in sentence.lower() and len(sentence) > 10:
                            decisions.append(sentence.strip()[:150])
                            break
                    break

        # إزالة التكرارات
        seen: set[str] = set()
        unique_decisions: list[str] = []
        for decision in decisions:
            if decision not in seen:
                seen.add(decision)
                unique_decisions.append(decision)

        return unique_decisions[:10]

    def _extract_action_items(self, messages: list[Message]) -> list[str]:
        """استخراج المهام المطلوبة من الرسائل."""
        action_items: list[str] = []
        action_indicators = [
            "يجب", "must", "should", "need to", "يتعين", "سوف ن",
            "will", "سوف", "نعمل على", "working on",
        ]

        for message in messages:
            content_lower = message.content.lower()
            for indicator in action_indicators:
                if indicator in content_lower:
                    sentences = message.content.split("。")
                    sentences.extend(message.content.split(". "))
                    for sentence in sentences:
                        if indicator in sentence.lower() and len(sentence) > 10:
                            action_items.append(sentence.strip()[:150])
                            break
                    break

        # إزالة التكرارات
        seen: set[str] = set()
        unique_items: list[str] = []
        for item in action_items:
            if item not in seen:
                seen.add(item)
                unique_items.append(item)

        return unique_items[:10]

    def _generate_summary_text(
        self,
        messages: list[Message],
        key_points: list[str],
        decisions: list[str],
        action_items: list[str],
    ) -> str:
        """توليد نص الملخص من الرسائل والملخصات الفرعية."""
        parts: list[str] = []

        # ملخص عام
        user_messages = [m for m in messages if m.role == "user"]
        assistant_messages = [m for m in messages if m.role == "assistant"]

        parts.append(f"محادثة تحتوي على {len(messages)} رسالة")
        parts.append(f"({len(user_messages)} رسالة من المستخدم، {len(assistant_messages)} رد من المساعد)")

        if key_points:
            parts.append("\nالنقاط الرئيسية:")
            for point in key_points[:5]:
                parts.append(f"• {point}")

        if decisions:
            parts.append("\nالقرارات المتخذة:")
            for decision in decisions[:5]:
                parts.append(f"• {decision}")

        if action_items:
            parts.append("\nالمهام المطلوبة:")
            for item in action_items[:5]:
                parts.append(f"• {item}")

        return "\n".join(parts)

    def get_summary(self, summary_id: str) -> ConversationSummary | None:
        """الحصول على ملخص بمعرفه."""
        return self._summaries.get(summary_id)

    def list_summaries(
        self,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> list[ConversationSummary]:
        """قائمة الملخصات مع تصفية اختيارية."""
        summaries = list(self._summaries.values())

        if conversation_id:
            summaries = [s for s in summaries if s.conversation_id == conversation_id]

        # ترتيب حسب وقت الإنشاء (الأحدث أولاً)
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries[:limit]

    def search_summaries(
        self,
        query: str,
        limit: int = 50,
    ) -> list[ConversationSummary]:
        """البحث في الملخصات بالنص."""
        query_lower = query.lower()
        results = []

        for summary in self._summaries.values():
            score = 0
            if query_lower in summary.summary.lower():
                score += 3
            if any(query_lower in point.lower() for point in summary.key_points):
                score += 2
            if any(query_lower in decision.lower() for decision in summary.decisions):
                score += 2
            if any(query_lower in item.lower() for item in summary.action_items):
                score += 1

            if score > 0:
                results.append((score, summary))

        # ترتيب حسب النتيجة (الأعلى أولاً)
        results.sort(key=lambda x: x[0], reverse=True)
        return [summary for _, summary in results[:limit]]

    def get_statistics(self) -> dict[str, Any]:
        """الحصول على إحصائيات الملخصات."""
        total = len(self._summaries)
        total_messages = sum(s.message_count for s in self._summaries.values())
        total_tokens = sum(s.token_count for s in self._summaries.values())

        return {
            "total_summaries": total,
            "total_messages_summarized": total_messages,
            "total_tokens_saved": total_tokens,
            "average_messages_per_summary": round(total_messages / max(1, total), 1),
        }

    def close(self) -> None:
        """إغلاق الملخصات (لا يوجد شيء للحفظ في الذاكرة الحالية)."""
        log.info("تم إغلاق ملخص المحادثات (%d ملخص)", len(self._summaries))
