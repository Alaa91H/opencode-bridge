"""Intent Analyzer - محلل النوايا الذكي.

يحلل البرومبتات لفهم النية، النطاق، المتطلبات، والتعقيد،
يساعد الوكيل على تخطيط التنفيذ الأمثل لكل مهمة.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger("opencode_bridge.agent_integration.intent_analyzer")


class IntentType(Enum):
    """أنواع النوايا الممكنة للبرومبتات."""

    CODE_GENERATION = "code_generation"  # توليد كود جديد
    CODE_REVIEW = "code_review"  # مراجعة كود موجود
    BUG_FIX = "bug_fix"  # إصلاح أخطاء
    REFACTORING = "refactoring"  # إعادة هيكلة كود
    DOCUMENTATION = "documentation"  # توثيق
    TESTING = "testing"  # اختبار
    DEPLOYMENT = "deployment"  # نشر
    RESEARCH = "research"  # بحث واستكشاف
    COMPLEX_TASK = "complex_task"  # مهمة معقدة متعددة الخطوات
    UNKNOWN = "unknown"  # غير معروف


class ComplexityLevel(Enum):
    """مستويات التعقيد للمهام."""

    SIMPLE = "simple"  # بسيط - خطوة واحدة
    MODERATE = "moderate"  # متوسط - خطوات قليلة
    COMPLEX = "complex"  # معقد - خطوات متعددة مع اعتمادات
    EXPERT = "expert"  # خبير - مهمة كبيرة تتطلب تخطيطاً دقيقاً


@dataclass
class Requirement:
    """متطلب واحد من متطلبات البرومبت."""

    id: str
    description: str
    type: str  # functional, non-functional, technical, constraint
    priority: int  # 1 = highest, 5 = lowest
    is_met: bool = False


@dataclass
class IntentAnalysis:
    """نتيجة تحليل النية للبرومبت."""

    intent_type: IntentType
    confidence: float  # 0.0 to 1.0
    complexity: ComplexityLevel
    estimated_time_minutes: int
    requirements: list[Requirement]
    key_entities: list[str]  # كيانات رئيسية (ملفات، دوال، مكتبات)
    suggested_agents: list[str]  # أنواع الوكلاء المقترحة
    estimated_tokens: int  # تقدير عدد التوكنات المطلوبة
    needs_file_access: bool  # هل يحتاج الوصول للملفات
    needs_internet: bool  # هل يحتاج اتصال إنترنت
    needs_build_tools: bool  # هل يحتاج أدوات بناء
    summary: str  # ملخص قصير للتحليل


class IntentAnalyzer:
    """محلل النوايا الذكي للبرومبتات.

    يحلل البرومبتات لتحديد نوع المهمة، النطاق، المتطلبات،
    والتعقيد، مما يساعد الوكيل على تخطيط التنفيذ الأمثل.
    """

    def __init__(self) -> None:
        # أنماط詞 للتعرف على أنواع النوايا
        self._intent_patterns: dict[IntentType, list[str]] = {
            IntentType.CODE_GENERATION: [
                r"اكتب كود", r"أنشئ دالة", r"اكتب برنامج", r"generate code",
                r"create function", r"write code", r"build application",
                r"تطبيق جديد", r"برنامج جديد",
            ],
            IntentType.CODE_REVIEW: [
                r"راجع الكود", r"مراجعة الكود", r"code review",
                r"تحقق من الكود", r"فحص الكود", r"أخطاء الكود",
            ],
            IntentType.BUG_FIX: [
                r"إصلاح", r"أصلح", r"bug", r"خطأ", r"fix", r"error",
                r"does not work", r"لا يعمل", r"مشكلة في",
            ],
            IntentType.REFACTORING: [
                r"إعادة هيكلة", r"refactor", r"تحسين الكود", r"clean code",
                r"أفضل الكود", r"تنظيم الكود",
            ],
            IntentType.DOCUMENTATION: [
                r"توثيق", r"documentation", r"اكتب توثيق", r"readme",
                r"دليل المستخدم", r"دليل الاستخدام",
            ],
            IntentType.TESTING: [
                r"اختبار", r"test", r"اختبار وحدة", r"unit test",
                r"اختبار تكامل", r"integration test",
            ],
            IntentType.DEPLOYMENT: [
                r"نشر", r"deploy", r"إطلاق", r"إنتاج", r"production",
                r"خادم", r"server",
            ],
            IntentType.RESEARCH: [
                r"ابحث", r"research", r"استكشف", r"استقصاء",
                r"معلومات عن", r"ما هو", r"كيف يعمل",
            ],
            IntentType.COMPLEX_TASK: [
                r"مشروع", r"project", r"نظام كامل", r"full system",
                r"بناء تطبيق", r"تطبيق كامل", r"نظام إدارة",
            ],
        }

        # كلمات دلالية للتعقيد
        self._complexity_indicators: dict[ComplexityLevel, list[str]] = {
            ComplexityLevel.SIMPLE: [
                r"بسيط", r"سريع", r"واحد", r"دالة واحدة", r"ملف واحد",
                r"simple", r"quick", r"one function", r"one file",
            ],
            ComplexityLevel.MODERATE: [
                r"متوسط", r"عدة دوال", r"عدة ملفات", r"moderate",
                r"several functions", r"several files",
            ],
            ComplexityLevel.COMPLEX: [
                r"معقد", r"معقد جداً", r"كثير", r"متعدد", r"complex",
                r"multiple", r"many files", r"كثير من الملفات",
            ],
            ComplexityLevel.EXPERT: [
                r"خبير", r"معماري", r"architecture", r"نظام كامل",
                r"full architecture", r"enterprise",
                r"نظام مؤسسي", r"large scale",
            ],
        }

    async def analyze_intent(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> IntentAnalysis:
        """تحليل البرومبت لتحديد النية، النطاق، والمتطلبات.

        Args:
            prompt: نص البرومبت للتحليل
            context: سياق إضافي (ملفات مرفقة، جلسة سابقة، إلخ)

        Returns:
            IntentAnalysis مع نتائج التحليل
        """
        context = context or {}
        prompt_lower = prompt.lower()

        # 1. تحديد نوع النية
        intent_type, confidence = self._classify_intent(prompt_lower)

        # 2. تقدير التعقيد
        complexity = self._estimate_complexity(prompt_lower, context)

        # 3. استخراج المتطلبات
        requirements = self._extract_requirements(prompt, intent_type)

        # 4. استخراج الكيانات الرئيسية
        key_entities = self._extract_key_entities(prompt)

        # 5. تقدير الوقت والتوكنات
        estimated_time = self._estimate_time(complexity, len(requirements))
        estimated_tokens = self._estimate_tokens(prompt, complexity)

        # 6. تحديد الاحتياجات التقنية
        needs_file_access = self._needs_file_access(prompt_lower)
        needs_internet = self._needs_internet(prompt_lower)
        needs_build_tools = self._needs_build_tools(prompt_lower)

        # 7. اقتراح وكلاء مناسبين
        suggested_agents = self._suggest_agents(intent_type, complexity)

        # 8. ملخص التحليل
        summary = self._generate_summary(intent_type, complexity, requirements)

        return IntentAnalysis(
            intent_type=intent_type,
            confidence=confidence,
            complexity=complexity,
            estimated_time_minutes=estimated_time,
            requirements=requirements,
            key_entities=key_entities,
            suggested_agents=suggested_agents,
            estimated_tokens=estimated_tokens,
            needs_file_access=needs_file_access,
            needs_internet=needs_internet,
            needs_build_tools=needs_build_tools,
            summary=summary,
        )

    def _classify_intent(
        self,
        prompt_lower: str,
    ) -> tuple[IntentType, float]:
        """تصنيف نوع النية بناءً على أنماط詞."""
        best_match = IntentType.UNKNOWN
        best_score = 0.0

        for intent_type, patterns in self._intent_patterns.items():
            matches = sum(1 for pattern in patterns if re.search(pattern, prompt_lower))
            if matches > 0:
                score = matches / len(patterns)
                if score > best_score:
                    best_score = score
                    best_match = intent_type

        # إذا لم يتطابق أي نمط، نعتبره غير معروف
        if best_score == 0.0:
            return IntentType.UNKNOWN, 0.0

        # ضبط الثقة بناءً على عدد الأنماط المطابقة
        confidence = min(0.95, best_score + 0.3)
        return best_match, confidence

    def _estimate_complexity(
        self,
        prompt_lower: str,
        context: dict[str, Any],
    ) -> ComplexityLevel:
        """تقدير تعقيد المهمة."""
        # عد مؤشرات التعقيد
        complexity_scores: dict[ComplexityLevel, int] = dict.fromkeys(ComplexityLevel, 0)

        for level, patterns in self._complexity_indicators.items():
            for pattern in patterns:
                if re.search(pattern, prompt_lower):
                    complexity_scores[level] += 1

        # اعتبار عوامل السياق
        file_count = context.get("file_count", 0)
        if file_count > 5:
            complexity_scores[ComplexityLevel.COMPLEX] += 1
        if file_count > 10:
            complexity_scores[ComplexityLevel.EXPERT] += 1

        prompt_length = len(prompt_lower)
        if prompt_length > 2000:
            complexity_scores[ComplexityLevel.COMPLEX] += 1
        if prompt_length > 5000:
            complexity_scores[ComplexityLevel.EXPERT] += 1

        # تحديد أعلى نتيجة
        max_level = ComplexityLevel.SIMPLE
        max_score = 0
        for level, score in complexity_scores.items():
            if score > max_score:
                max_score = score
                max_level = level

        return max_level

    def _extract_requirements(
        self,
        prompt: str,
        intent_type: IntentType,
    ) -> list[Requirement]:
        """استخراج المتطلبات من البرومبت."""
        requirements: list[Requirement] = []
        req_id = 1

        # متطلبات عامة حسب نوع النية
        if intent_type == IntentType.CODE_GENERATION:
            requirements.append(Requirement(
                id=f"req_{req_id}",
                description="توليد كود يعمل بدون أخطاء",
                type="functional",
                priority=1,
            ))
            req_id += 1
            requirements.append(Requirement(
                id=f"req_{req_id}",
                description="توثيق الكود المولد",
                type="non-functional",
                priority=3,
            ))
            req_id += 1

        if intent_type == IntentType.BUG_FIX:
            requirements.append(Requirement(
                id=f"req_{req_id}",
                description="تحديد سبب الخطأ",
                type="functional",
                priority=1,
            ))
            req_id += 1
            requirements.append(Requirement(
                id=f"req_{req_id}",
                description="إصلاح الخطأ دون إدخال أخطاء جديدة",
                type="functional",
                priority=2,
            ))
            req_id += 1

        if intent_type == IntentType.RESEARCH:
            requirements.append(Requirement(
                id=f"req_{req_id}",
                description="جمع معلومات دقيقة وموثقة",
                type="functional",
                priority=1,
            ))
            req_id += 1
            requirements.append(Requirement(
                id=f"req_{req_id}",
                description="توفير مصادر ومراجع",
                type="non-functional",
                priority=2,
            ))
            req_id += 1

        # متطلبات عامة لجميع المهام
        requirements.append(Requirement(
            id=f"req_{req_id}",
            description="إنتاج ناتج واضح ومفهوم",
            type="non-functional",
            priority=2,
        ))

        return requirements

    def _extract_key_entities(self, prompt: str) -> list[str]:
        """استخراج الكيانات الرئيسية (ملفات، دوال، مكتبات)."""
        entities: list[str] = []

        # استخراج أسماء الملفات مع الامتدادات
        file_patterns = re.findall(r"\b[\w\-]+\.\w{2,4}\b", prompt)
        entities.extend(file_patterns[:10])

        # استخراج أسماء الدوال (أنماط شائعة)
        func_patterns = re.findall(r"\b(def|function|class)\s+(\w+)", prompt)
        for _, name in func_patterns[:10]:
            entities.append(name)

        # استخراج أسماء المكتبات (أنماط import/require)
        lib_patterns = re.findall(r"\b(import|require|from)\s+([\w\-]+)", prompt)
        for _, lib in lib_patterns[:10]:
            entities.append(lib)

        # إزالة التكرارات مع الحفاظ على الترتيب
        seen: set[str] = set()
        unique_entities: list[str] = []
        for entity in entities:
            if entity not in seen:
                seen.add(entity)
                unique_entities.append(entity)

        return unique_entities[:15]

    def _estimate_time(
        self,
        complexity: ComplexityLevel,
        requirement_count: int,
    ) -> int:
        """تقدير الوقت المتوقع بالدقائق."""
        base_times = {
            ComplexityLevel.SIMPLE: 5,
            ComplexityLevel.MODERATE: 15,
            ComplexityLevel.COMPLEX: 45,
            ComplexityLevel.EXPERT: 120,
        }
        base_time = base_times.get(complexity, 15)
        # إضافة وقت لكل متطلب إضافي
        extra_time = max(0, requirement_count - 3) * 5
        return base_time + extra_time

    def _estimate_tokens(
        self,
        prompt: str,
        complexity: ComplexityLevel,
    ) -> int:
        """تقدير عدد التوكنات المطلوبة للمعالجة."""
        base_tokens = len(prompt) // 4  # تقدير تقريبي
        complexity_multiplier = {
            ComplexityLevel.SIMPLE: 1.0,
            ComplexityLevel.MODERATE: 1.5,
            ComplexityLevel.COMPLEX: 2.5,
            ComplexityLevel.EXPERT: 4.0,
        }
        multiplier = complexity_multiplier.get(complexity, 1.5)
        return int(base_tokens * multiplier)

    def _needs_file_access(self, prompt_lower: str) -> bool:
        """تحديد ما إذا كان البرومبت يحتاج الوصول للملفات."""
        file_indicators = [
            r"ملف", r"file", r"قراءة", r"read", r"كتابة", r"write",
            r"تعديل", r"edit", r"مسار", r"path", r"دليل", r"folder",
        ]
        return any(re.search(pattern, prompt_lower) for pattern in file_indicators)

    def _needs_internet(self, prompt_lower: str) -> bool:
        """تحديد ما إذا كان البرومبت يحتاج اتصال إنترنت."""
        internet_indicators = [
            r"بحث", r"research", r"إنترنت", r"internet", r"ويب", r"web",
            r"موقع", r"site", r"رابط", r"url", r"api", r"واجهة برمجة",
        ]
        return any(re.search(pattern, prompt_lower) for pattern in internet_indicators)

    def _needs_build_tools(self, prompt_lower: str) -> bool:
        """تحديد ما إذا كان البرومبت يحتاج أدوات بناء."""
        build_indicators = [
            r"بناء", r"build", r"تجميع", r"compile", r"تثبيت", r"install",
            r"npm", r"pip", r"make", r"cmake", r"gradle", r"mvn",
        ]
        return any(re.search(pattern, prompt_lower) for pattern in build_indicators)

    def _suggest_agents(
        self,
        intent_type: IntentType,
        complexity: ComplexityLevel,
    ) -> list[str]:
        """اقتراح وكلاء مناسبين بناءً على النوع والتعقيد."""
        agent_map: dict[IntentType, list[str]] = {
            IntentType.CODE_GENERATION: ["development-agent", "code-agent"],
            IntentType.CODE_REVIEW: ["reviewer-agent", "quality-agent"],
            IntentType.BUG_FIX: ["debugger-agent", "fix-agent"],
            IntentType.REFACTORING: ["refactor-agent", "clean-code-agent"],
            IntentType.DOCUMENTATION: ["writer-agent", "doc-agent"],
            IntentType.TESTING: ["tester-agent", "qa-agent"],
            IntentType.DEPLOYMENT: ["deploy-agent", "devops-agent"],
            IntentType.RESEARCH: ["researcher-agent", "analyst-agent"],
            IntentType.COMPLEX_TASK: ["architect-agent", "planner-agent"],
            IntentType.UNKNOWN: ["general-agent"],
        }
        return agent_map.get(intent_type, ["general-agent"])

    def _generate_summary(
        self,
        intent_type: IntentType,
        complexity: ComplexityLevel,
        requirements: list[Requirement],
    ) -> str:
        """توليد ملخص قصير للتحليل."""
        req_count = len(requirements)
        high_priority = sum(1 for r in requirements if r.priority <= 2)

        return (
            f"نية: {intent_type.value} | التعقيد: {complexity.value} | "
            f"المتطلبات: {req_count} ({high_priority} عالية الأولوية)"
        )
