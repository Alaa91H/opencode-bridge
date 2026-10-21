"""Smart Execution Planner - مخطط التنفيذ الذكي.

يخطط تنفيذ المهام المعقدة بخطوات متسلسلة أو متوازية مع تحديد
الاعتمادات بين المهام الفرعية.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from bridge.services.agent_integration.intent_analyzer import (
    ComplexityLevel,
    IntentAnalysis,
    IntentType,
    Requirement,
)

log = logging.getLogger("opencode_bridge.agent_integration.execution_planner")


class ExecutionStepStatus(Enum):
    """حالات خطوات التنفيذ."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Subtask:
    """مهمة فرعية داخل خطة التنفيذ."""

    id: str
    title: str
    description: str
    intent_type: IntentType
    complexity: ComplexityLevel
    estimated_time_minutes: int
    dependencies: list[str] = field(default_factory=list)  # معرفات مهام فرعية سابقة
    status: ExecutionStepStatus = ExecutionStepStatus.PENDING
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    agent_suggestion: str | None = None  # الوكيل المقترح لتنفيذ هذه المهمة
    files_involved: list[str] = field(default_factory=list)  # ملفات متورطة
    commands_to_run: list[str] = field(default_factory=list)  # أوامر يجب تشغيلها


@dataclass
class ExecutionPlan:
    """خطة تنفيذ مفصلة للمهمة الرئيسية."""

    id: str
    title: str
    description: str
    overall_intent: IntentType
    overall_complexity: ComplexityLevel
    estimated_total_time_minutes: int
    subtasks: list[Subtask]
    dependency_graph: dict[str, list[str]]  # task_id -> list of dependency ids
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: str = "planned"  # planned, running, completed, failed
    results: dict[str, str] = field(default_factory=dict)  # task_id -> result

    @property
    def completed_subtasks(self) -> int:
        return sum(1 for t in self.subtasks if t.status == ExecutionStepStatus.COMPLETED)

    @property
    def total_subtasks(self) -> int:
        return len(self.subtasks)

    @property
    def progress_percentage(self) -> float:
        if self.total_subtasks == 0:
            return 0.0
        return round((self.completed_subtasks / self.total_subtasks) * 100, 1)

    def get_ready_subtasks(self) -> list[Subtask]:
        """الحصول على المهام الفرعية الجاهزة للتنفيذ (جميع الاعتمادات مكتملة)."""
        completed_ids = {
            t.id for t in self.subtasks if t.status == ExecutionStepStatus.COMPLETED
        }
        ready = []
        for subtask in self.subtasks:
            if subtask.status == ExecutionStepStatus.PENDING:
                if all(dep_id in completed_ids for dep_id in subtask.dependencies):
                    ready.append(subtask)
        return ready

    def is_complete(self) -> bool:
        """التحقق من اكتمال جميع المهام الفرعية."""
        return all(
            t.status in (ExecutionStepStatus.COMPLETED, ExecutionStepStatus.SKIPPED)
            for t in self.subtasks
        )

    def has_failures(self) -> bool:
        """التحقق من وجود فشل في أي مهمة فرعية."""
        return any(t.status == ExecutionStepStatus.FAILED for t in self.subtasks)


class SmartExecutionPlanner:
    """مخطط التنفيذ الذكي للمهام المعقدة.

    يقسم المهام الكبيرة إلى مهام فرعية قابلة للإدارة مع تحديد
    الاعتمادات بينها وترتيب التنفيذ الأمثل.
    """

    def __init__(self) -> None:
        self._plans: dict[str, ExecutionPlan] = {}

    async def create_execution_plan(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        available_tools: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """إنشاء خطة تنفيذ مفصلة للمهمة.

        Args:
            intent: نتيجة تحليل النية
            requirements: قائمة المتطلبات
            available_tools: الأدوات المتاحة للتنفيذ
            context: سياق إضافي (ملفات مرفقة، جلسة سابقة، إلخ)

        Returns:
            ExecutionPlan مع خطة التنفيذ المفصلة
        """
        context = context or {}
        available_tools = available_tools or []

        # تقسيم المهمة إلى مهام فرعية
        subtasks = await self._break_down_task(
            intent, requirements, available_tools, context
        )

        # تحديد الاعتمادات بين المهام الفرعية
        dependency_graph = self._identify_dependencies(subtasks)

        # تقدير الوقت الإجمالي
        total_time = sum(s.estimated_time_minutes for s in subtasks)

        # إنشاء الخطة
        plan = ExecutionPlan(
            id=str(uuid.uuid4()),
            title=intent.summary[:100] if intent.summary else "مهمة غير محددة",
            description=intent.summary,
            overall_intent=intent.intent_type,
            overall_complexity=intent.complexity,
            estimated_total_time_minutes=total_time,
            subtasks=subtasks,
            dependency_graph=dependency_graph,
        )

        self._plans[plan.id] = plan
        log.info(
            "تم إنشاء خطة تنفيذ %s مع %d مهمة فرعية (الوقت المقدر: %d دقيقة)",
            plan.id,
            len(subtasks),
            total_time,
        )
        return plan

    async def _break_down_task(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        available_tools: list[str],
        context: dict[str, Any],
    ) -> list[Subtask]:
        """تقسيم المهمة الكبيرة إلى مهام فرعية."""

        subtasks: list[Subtask] = []
        subtask_id = 1

        # بناءً على نوع النية، نقسم المهمة بشكل مختلف
        if intent.intent_type == IntentType.CODE_GENERATION:
            subtasks.extend(self._plan_code_generation(intent, requirements, subtask_id))
        elif intent.intent_type == IntentType.BUG_FIX:
            subtasks.extend(self._plan_bug_fix(intent, requirements, subtask_id))
        elif intent.intent_type == IntentType.RESEARCH:
            subtasks.extend(self._plan_research(intent, requirements, subtask_id))
        elif intent.intent_type == IntentType.REFACTORING:
            subtasks.extend(self._plan_refactoring(intent, requirements, subtask_id))
        elif intent.intent_type == IntentType.DEPLOYMENT:
            subtasks.extend(self._plan_deployment(intent, requirements, subtask_id))
        elif intent.intent_type == IntentType.TESTING:
            subtasks.extend(self._plan_testing(intent, requirements, subtask_id))
        elif intent.intent_type == IntentType.DOCUMENTATION:
            subtasks.extend(self._plan_documentation(intent, requirements, subtask_id))
        elif intent.intent_type == IntentType.COMPLEX_TASK:
            subtasks.extend(self._plan_complex_task(intent, requirements, subtask_id))
        else:
            # مهمة عامة - مهمة واحدة
            subtasks.append(Subtask(
                id=f"subtask_{subtask_id}",
                title="تنفيذ البرومبت",
                description=intent.summary,
                intent_type=intent.intent_type,
                complexity=intent.complexity,
                estimated_time_minutes=intent.estimated_time_minutes,
                agent_suggestion=intent.suggested_agents[0] if intent.suggested_agents else "general-agent",
                files_involved=intent.key_entities[:5],
            ))

        return subtasks

    def _plan_code_generation(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        start_id: int,
    ) -> list[Subtask]:
        """تخطيط مهمة توليد كود."""
        subtasks: list[Subtask] = []
        sid = start_id

        # المهمة 1: تحليل المتطلبات وتصميم الحل
        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="تحليل المتطلبات وتصميم الحل",
            description="فهم المتطلبات وتصميم هيكل الكود قبل التنفيذ",
            intent_type=IntentType.CODE_GENERATION,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=15,
            agent_suggestion="architect-agent",
            files_involved=intent.key_entities[:3],
        ))
        sid += 1

        # المهمة 2: كتابة الكود الأساسي
        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="كتابة الكود الأساسي",
            description="تنفيذ الكود الرئيسي بناءً على التصميم",
            intent_type=IntentType.CODE_GENERATION,
            complexity=intent.complexity,
            estimated_time_minutes=intent.estimated_time_minutes,
            dependencies=[f"subtask_{start_id}"],
            agent_suggestion="development-agent",
            files_involved=intent.key_entities,
        ))
        sid += 1

        # المهمة 3: اختبار الكود
        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="اختبار الكود المولد",
            description="التحقق من عمل الكود بشكل صحيح",
            intent_type=IntentType.TESTING,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=20,
            dependencies=[f"subtask_{start_id + 1}"],
            agent_suggestion="tester-agent",
        ))

        return subtasks

    def _plan_bug_fix(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        start_id: int,
    ) -> list[Subtask]:
        """تخطيط مهمة إصلاح خطأ."""
        subtasks: list[Subtask] = []
        sid = start_id

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="تشخيص الخطأ",
            description="تحديد سبب الخطأ وموقعه في الكود",
            intent_type=IntentType.BUG_FIX,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=30,
            agent_suggestion="debugger-agent",
            files_involved=intent.key_entities[:5],
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="إصلاح الخطأ",
            description="تطبيق الإصلاح على الكود",
            intent_type=IntentType.BUG_FIX,
            complexity=intent.complexity,
            estimated_time_minutes=intent.estimated_time_minutes,
            dependencies=[f"subtask_{start_id}"],
            agent_suggestion="debugger-agent",
            files_involved=intent.key_entities[:5],
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="التحقق من الإصلاح",
            description="التأكد من إصلاح الخطأ وعدم ظهور مشاكل جديدة",
            intent_type=IntentType.TESTING,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=15,
            dependencies=[f"subtask_{start_id + 1}"],
            agent_suggestion="tester-agent",
        ))

        return subtasks

    def _plan_research(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        start_id: int,
    ) -> list[Subtask]:
        """تخطيط مهمة بحث."""
        subtasks: list[Subtask] = []
        sid = start_id

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="جمع المعلومات",
            description="البحث عن معلومات دقيقة وموثقة حول الموضوع",
            intent_type=IntentType.RESEARCH,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=30,
            agent_suggestion="researcher-agent",
            needs_internet=True,
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="تحليل وترتيب المعلومات",
            description="تحليل المعلومات المجمعة وتنسيقها",
            intent_type=IntentType.RESEARCH,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=20,
            dependencies=[f"subtask_{start_id}"],
            agent_suggestion="analyst-agent",
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="إعداد التقرير النهائي",
            description="كتابة تقرير شامل بالنتائج والمصادر",
            intent_type=IntentType.DOCUMENTATION,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=25,
            dependencies=[f"subtask_{start_id + 1}"],
            agent_suggestion="writer-agent",
        ))

        return subtasks

    def _plan_refactoring(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        start_id: int,
    ) -> list[Subtask]:
        """تخطيط مهمة إعادة هيكلة."""
        subtasks: list[Subtask] = []
        sid = start_id

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="تحليل الكود الحالي",
            description="فهم هيكل الكود الحالي وتحديد مناطق التحسين",
            intent_type=IntentType.CODE_REVIEW,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=20,
            agent_suggestion="reviewer-agent",
            files_involved=intent.key_entities[:5],
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="إعادة هيكلة الكود",
            description="تحسين هيكل الكود دون تغيير السلوك",
            intent_type=IntentType.REFACTORING,
            complexity=intent.complexity,
            estimated_time_minutes=intent.estimated_time_minutes,
            dependencies=[f"subtask_{start_id}"],
            agent_suggestion="refactor-agent",
            files_involved=intent.key_entities[:5],
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="اختبار الكود المعاد هيكلته",
            description="التأكد من أن الكود لا يزال يعمل بشكل صحيح",
            intent_type=IntentType.TESTING,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=20,
            dependencies=[f"subtask_{start_id + 1}"],
            agent_suggestion="tester-agent",
        ))

        return subtasks

    def _plan_deployment(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        start_id: int,
    ) -> list[Subtask]:
        """تخطيط مهمة النشر."""
        subtasks: list[Subtask] = []
        sid = start_id

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="تحضير环境 النشر",
            description="إعداد بيئة النشر والتحقق من المتطلبات",
            intent_type=IntentType.DEPLOYMENT,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=20,
            agent_suggestion="devops-agent",
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="نشر التطبيق",
            description="نشر الكود على بيئة الإنتاج",
            intent_type=IntentType.DEPLOYMENT,
            complexity=intent.complexity,
            estimated_time_minutes=intent.estimated_time_minutes,
            dependencies=[f"subtask_{start_id}"],
            agent_suggestion="devops-agent",
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="التحقق من النشر",
            description="التأكد من أن التطبيق يعمل بشكل صحيح بعد النشر",
            intent_type=IntentType.TESTING,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=15,
            dependencies=[f"subtask_{start_id + 1}"],
            agent_suggestion="tester-agent",
        ))

        return subtasks

    def _plan_testing(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        start_id: int,
    ) -> list[Subtask]:
        """تخطيط مهمة اختبار."""
        subtasks: list[Subtask] = []
        sid = start_id

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="تصميم حالات الاختبار",
            description="تصميم حالات اختبار تغطي جميع السيناريوهات",
            intent_type=IntentType.TESTING,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=25,
            agent_suggestion="qa-agent",
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="تنفيذ الاختبارات",
            description="تشغيل حالات الاختبار وتسجيل النتائج",
            intent_type=IntentType.TESTING,
            complexity=intent.complexity,
            estimated_time_minutes=intent.estimated_time_minutes,
            dependencies=[f"subtask_{start_id}"],
            agent_suggestion="tester-agent",
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="إعداد تقرير الاختبار",
            description="كتابة تقرير شامل بنتائج الاختبار",
            intent_type=IntentType.DOCUMENTATION,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=15,
            dependencies=[f"subtask_{start_id + 1}"],
            agent_suggestion="qa-agent",
        ))

        return subtasks

    def _plan_documentation(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        start_id: int,
    ) -> list[Subtask]:
        """تخطيط مهمة توثيق."""
        subtasks: list[Subtask] = []
        sid = start_id

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="جمع المعلومات للتوثيق",
            description="جمع جميع المعلومات اللازمة للتوثيق",
            intent_type=IntentType.RESEARCH,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=20,
            agent_suggestion="researcher-agent",
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="كتابة التوثيق",
            description="كتابة توثيق واضح ومفصل",
            intent_type=IntentType.DOCUMENTATION,
            complexity=intent.complexity,
            estimated_time_minutes=intent.estimated_time_minutes,
            dependencies=[f"subtask_{start_id}"],
            agent_suggestion="writer-agent",
        ))
        sid += 1

        subtasks.append(Subtask(
            id=f"subtask_{sid}",
            title="مراجعة التوثيق",
            description="مراجعة التوثيق للتأكد من دقته وشموليته",
            intent_type=IntentType.CODE_REVIEW,
            complexity=ComplexityLevel.MODERATE,
            estimated_time_minutes=15,
            dependencies=[f"subtask_{start_id + 1}"],
            agent_suggestion="reviewer-agent",
        ))

        return subtasks

    def _plan_complex_task(
        self,
        intent: IntentAnalysis,
        requirements: list[Requirement],
        start_id: int,
    ) -> list[Subtask]:
        """تخطيط مهمة معقدة متعددة الخطوات."""
        subtasks: list[Subtask] = []
        sid = start_id

        # للمهام المعقدة، نقسمها إلى مراحل رئيسية
        phases = [
            ("التخطيط والتصميم", "تحديد المتطلبات وتصميم الحل العام", IntentType.COMPLEX_TASK),
            ("التنفيذ הראשي", "تنفيذ المكونات الأساسية للمشروع", intent.intent_type),
            ("الاختبار والتحقق", "اختبار جميع المكونات والتحقق من جودتها", IntentType.TESTING),
            ("التوثيق والنشر", "توثيق المشروع ونشره للمستخدمين", IntentType.DOCUMENTATION),
        ]

        prev_subtask_id = None
        for phase_title, phase_desc, phase_intent in phases:
            subtasks.append(Subtask(
                id=f"subtask_{sid}",
                title=phase_title,
                description=phase_desc,
                intent_type=phase_intent,
                complexity=intent.complexity,
                estimated_time_minutes=max(30, intent.estimated_time_minutes // len(phases)),
                dependencies=[prev_subtask_id] if prev_subtask_id else [],
                agent_suggestion="architect-agent" if phase_title == "التخطيط والتصميم" else (
                    intent.suggested_agents[0] if intent.suggested_agents else "general-agent"
                ),
                files_involved=intent.key_entities[:5],
            ))
            prev_subtask_id = f"subtask_{sid}"
            sid += 1

        return subtasks

    def _identify_dependencies(
        self,
        subtasks: list[Subtask],
    ) -> dict[str, list[str]]:
        """تحديد رسم الاعتمادات بين المهام الفرعية."""
        graph: dict[str, list[str]] = {}
        for subtask in subtasks:
            graph[subtask.id] = list(subtask.dependencies)
        return graph

    def get_plan(self, plan_id: str) -> ExecutionPlan | None:
        """الحصول على خطة تنفيذ بمعرفها."""
        return self._plans.get(plan_id)

    async def update_subtask_status(
        self,
        plan_id: str,
        subtask_id: str,
        status: ExecutionStepStatus,
        result: str | None = None,
        error: str | None = None,
    ) -> bool:
        """تحديث حالة مهمة فرعية."""
        plan = self._plans.get(plan_id)
        if plan is None:
            return False

        for subtask in plan.subtasks:
            if subtask.id == subtask_id:
                subtask.status = status
                if result is not None:
                    subtask.result = result
                    plan.results[subtask_id] = result
                if error is not None:
                    subtask.error = error
                if status == ExecutionStepStatus.RUNNING:
                    subtask.started_at = datetime.now(UTC)
                if status in (ExecutionStepStatus.COMPLETED, ExecutionStepStatus.FAILED):
                    subtask.completed_at = datetime.now(UTC)

                # تحديث حالة الخطة العامة
                if plan.is_complete():
                    plan.status = "completed"
                    plan.completed_at = datetime.now(UTC)
                elif plan.has_failures():
                    plan.status = "failed"

                log.info(
                    "تم ت重要يت مهمة فرعية %s في الخطة %s: %s",
                    subtask_id,
                    plan_id,
                    status.value,
                )
                return True

        return False

    def get_next_ready_subtasks(self, plan_id: str) -> list[Subtask]:
        """الحصول على المهام الفرعية الجاهزة للتنفيذ التالي."""
        plan = self._plans.get(plan_id)
        if plan is None:
            return []
        return plan.get_ready_subtasks()
