"""Agent System Integration Engine - محرك التكامل بين الوكيل والنظام.

يوفر تكاملاً ثنائي الاتجاه بين الوكيل والنظام:
- الوكيل يطلب إجراءات من النظام (إنشاء ملف، تشغيل أمر، إلخ)
- النظام يُعلم الوكيل بالأحداث المهمة (اكتمال استنساخ، فشل مهمة، إلخ)
- الوكيل يتحقق من مخرجات النظام قبل اعتبار المهمة مكتملة
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bridge.services.agent_integration.execution_planner import (
    ExecutionPlan,
    ExecutionStepStatus,
    SmartExecutionPlanner,
    Subtask,
)
from bridge.services.agent_integration.intent_analyzer import (
    IntentAnalysis,
    IntentAnalyzer,
)
from bridge.services.prompt_scheduler.scheduler_engine import (
    Priority,
    ScheduleType,
    SmartPromptScheduler,
)

log = logging.getLogger("opencode_bridge.agent_integration.integration_engine")


@dataclass
class SystemActionRequest:
    """طلب إجراء من النظام من قبل الوكيل."""

    id: str
    agent_id: str
    action_type: str  # create_file, read_file, run_command, create_session, etc.
    parameters: dict[str, Any]
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "pending"  # pending, running, completed, failed
    result: Any = None
    error: str | None = None


@dataclass
class SystemEvent:
    """حدث من النظام يُرسل للوكيل."""

    id: str
    event_type: str  # task_completed, task_failed, repo_cloned, build_finished, etc.
    data: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    acknowledged: bool = False


@dataclass
class ValidationResult:
    """نتيجة التحقق من مخرجات النظام."""

    is_valid: bool
    confidence: float  # 0.0 to 1.0
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


class AgentSystemIntegration:
    """محرك التكامل بين الوكيل والنظام.

    يوفر واجهة موحدة للتواصل بين الوكيل والنظام، مما يمكن الوكيل من:
    1. طلب إجراءات من النظام
    2. تلقي أحداث من النظام
    3. التحقق من مخرجات النظام
    """

    def __init__(
        self,
        intent_analyzer: IntentAnalyzer | None = None,
        execution_planner: SmartExecutionPlanner | None = None,
        prompt_scheduler: SmartPromptScheduler | None = None,
    ) -> None:
        self.intent_analyzer = intent_analyzer or IntentAnalyzer()
        self.execution_planner = execution_planner or SmartExecutionPlanner()
        self.prompt_scheduler = prompt_scheduler or SmartPromptScheduler()

        # حالة النظام
        self._system_actions: dict[str, SystemActionRequest] = {}
        self._system_events: list[SystemEvent] = []
        self._agent_sessions: dict[str, dict[str, Any]] = {}  # agent_id -> session data

        # معالجات الإجراءات المسجلة
        self._action_handlers: dict[str, Any] = {}

        # مؤشرات التشغيل
        self._integration_running = False
        self._event_processor_task: asyncio.Task | None = None

    # ========================================================================
    # تحليل البرومبت وإنشاء خطة تنفيذ
    # ========================================================================

    async def analyze_and_plan(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
        available_tools: list[str] | None = None,
    ) -> tuple[IntentAnalysis, ExecutionPlan]:
        """تحليل البرومبت وإنشاء خطة تنفيذ ذكية.

        هذه هي الدالة الرئيسية التي تربط بين تحليل النية وتخطيط التنفيذ.
        """
        context = context or {}

        # 1. تحليل النية
        intent = await self.intent_analyzer.analyze_intent(prompt, context)
        log.info(
            "تم تحليل النية: %s (الثقة: %.2f, التعقيد: %s)",
            intent.intent_type.value,
            intent.confidence,
            intent.complexity.value,
        )

        # 2. إنشاء خطة تنفيذ
        plan = await self.execution_planner.create_execution_plan(
            intent=intent,
            requirements=intent.requirements,
            available_tools=available_tools,
            context=context,
        )
        log.info(
            "تم إنشاء خطة تنفيذ %s مع %d مهمة فرعية",
            plan.id,
            plan.total_subtasks,
        )

        return intent, plan

    async def execute_plan(
        self,
        plan: ExecutionPlan,
        agent_executor: Any,
        auto_schedule: bool = True,
    ) -> str:
        """تنفيذ خطة التنفيذ باستخدام الوكيل.

        Args:
            plan: خطة التنفيذ
            agent_executor: المنفذ الذي سيقوم بتنفيذ المهام (الوكيل)
            auto_schedule: ما إذا كان يجب جدولة المهام عبر المجدول الذكي

        Returns:
            معرف خطة التنفيذ
        """
        plan.status = "running"
        plan.started_at = datetime.now(UTC)

        if auto_schedule and self.prompt_scheduler:
            # جدولة المهمة الأولى عبر المجدول
            await self._schedule_plan_execution(plan, agent_executor)
        else:
            # تنفيذ مباشر
            _task = asyncio.create_task(  # noqa: RUF006
                self._execute_plan_directly(plan, agent_executor)
            )

        return plan.id

    async def _schedule_plan_execution(
        self,
        plan: ExecutionPlan,
        agent_executor: Any,
    ) -> None:
        """جدولة تنفيذ خطة التنفيذ عبر المجدول الذكي."""
        # جدولة المهمة الأولى (التي ليس لها اعتمادات)
        ready_subtasks = plan.get_ready_subtasks()
        for subtask in ready_subtasks:
            await self.prompt_scheduler.schedule_prompt(
                prompt=subtask.description,
                schedule_type=ScheduleType.IMMEDIATE,
                priority=Priority.HIGH if subtask.complexity.value in ("complex", "expert") else Priority.NORMAL,
                metadata={
                    "plan_id": plan.id,
                    "subtask_id": subtask.id,
                    "agent_suggestion": subtask.agent_suggestion,
                },
            )

    async def _execute_plan_directly(
        self,
        plan: ExecutionPlan,
        agent_executor: Any,
    ) -> None:
        """تنفيذ خطة التنفيذ بشكل مباشر."""
        while not plan.is_complete():
            # الحصول على المهام الجاهزة
            ready_subtasks = plan.get_next_ready_subtasks(plan.id)
            if not ready_subtasks:
                if plan.has_failures():
                    log.error("خطة %s تحتوي على مهام فاشلة", plan.id)
                    break
                await asyncio.sleep(1)
                continue

            # تنفيذ المهمة الأولى الجاهزة
            subtask = ready_subtasks[0]
            await self._execute_subtask(plan, subtask, agent_executor)

        if plan.is_complete() and not plan.has_failures():
            plan.status = "completed"
            log.info("تم إكمال الخطة %s بنجاح", plan.id)
        elif plan.has_failures():
            plan.status = "failed"
            log.error("فشلت الخطة %s بسبب فشل في مهام فرعية", plan.id)

    async def _execute_subtask(
        self,
        plan: ExecutionPlan,
        subtask: Subtask,
        agent_executor: Any,
    ) -> None:
        """تنفيذ مهمة فرعية واحدة."""
        await self.execution_planner.update_subtask_status(
            plan.id,
            subtask.id,
            ExecutionStepStatus.RUNNING,
        )

        try:
            # استدعاء المنفذ لتنفيذ المهمة
            if hasattr(agent_executor, "execute_subtask"):
                result = await agent_executor.execute_subtask(subtask)
            elif hasattr(agent_executor, "run"):
                result = await agent_executor.run(subtask.description)
            else:
                # محاكاة التنفيذ إذا لم يتوفر منفذ
                result = f"تم تنفيذ المهمة {subtask.id} (محاكاة)"

            await self.execution_planner.update_subtask_status(
                plan.id,
                subtask.id,
                ExecutionStepStatus.COMPLETED,
                result=str(result),
            )
            log.info("تم إكمال المهمة الفرعية %s", subtask.id)

        except Exception as exc:
            await self.execution_planner.update_subtask_status(
                plan.id,
                subtask.id,
                ExecutionStepStatus.FAILED,
                error=str(exc),
            )
            log.error("فشلت المهمة الفرعية %s: %s", subtask.id, exc)

    # ========================================================================
    # إجراءات النظام (الوكيل يطلب من النظام)
    # ========================================================================

    async def request_system_action(
        self,
        agent_id: str,
        action_type: str,
        parameters: dict[str, Any],
    ) -> SystemActionRequest:
        """طلب إجراء من النظام من قبل الوكيل.

        Args:
            agent_id: معرف الوكيل الطالب
            action_type: نوع الإجراء (create_file, read_file, run_command, etc.)
            parameters: معاملات الإجراء

        Returns:
            كائن طلب الإجراء مع معرفته
        """
        action = SystemActionRequest(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            action_type=action_type,
            parameters=parameters,
        )
        self._system_actions[action.id] = action
        log.info(
            "تم تسجيل إجراء نظام %s من قبل الوكيل %s: %s",
            action.id,
            agent_id,
            action_type,
        )

        # تنفيذ الإجراء إذا كان هناك معالج مسجل
        if action_type in self._action_handlers:
            handler = self._action_handlers[action_type]
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(action.parameters)
                else:
                    result = handler(action.parameters)
                action.status = "completed"
                action.result = result
            except Exception as exc:
                action.status = "failed"
                action.error = str(exc)
                log.error("فشل تنفيذ إجراء النظام %s: %s", action.id, exc)
        else:
            log.warning("لا يوجد معالج للإجراء %s", action_type)

        return action

    def register_action_handler(
        self,
        action_type: str,
        handler: Any,
    ) -> None:
        """تسجيل معالج لنوع إجراء معين."""
        self._action_handlers[action_type] = handler
        log.info("تم تسجيل معالج إجراء: %s", action_type)

    async def get_action_result(self, action_id: str) -> SystemActionRequest | None:
        """الحصول على نتيجة إجراء نظام."""
        return self._system_actions.get(action_id)

    # ========================================================================
    # أحداث النظام (النظام يُعلم الوكيل)
    # ========================================================================

    async def emit_system_event(
        self,
        event_type: str,
        data: dict[str, Any],
    ) -> str:
        """إطلاق حدث من النظام لإعلام الوكلاء."""
        event = SystemEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            data=data,
        )
        self._system_events.append(event)
        log.info(
            "تم إطلاق حدث نظام %s: %s",
            event.id,
            event_type,
        )
        return event.id

    async def get_unacknowledged_events(
        self,
        agent_id: str | None = None,
    ) -> list[SystemEvent]:
        """الحصول على الأحداث غير المؤكدة للوكيل."""
        return [
            event for event in self._system_events
            if not event.acknowledged
        ]

    async def acknowledge_event(self, event_id: str) -> bool:
        """تأكيد استقبال حدث."""
        for event in self._system_events:
            if event.id == event_id:
                event.acknowledged = True
                return True
        return False

    # ========================================================================
    # التحقق من مخرجات النظام
    # ========================================================================

    async def validate_system_output(
        self,
        agent_id: str,
        output: str,
        expected_criteria: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """التحقق من مخرجات النظام قبل اعتبار المهمة مكتملة.

        Args:
            agent_id: معرف الوكيل
            output: المخرجات التي يجب التحقق منها
            expected_criteria: معايير التوقع المتوقعة
            context: سياق إضافي للمساعدة في التحقق

        Returns:
            نتيجة التحقق مع مستوى الثقة والمشاكل المقترحة
        """
        context = context or {}
        expected_criteria = expected_criteria or []

        issues: list[str] = []
        suggestions: list[str] = []
        confidence = 1.0

        # التحقق الأساسي من وجود مخرجات
        if not output or not output.strip():
            issues.append("المخرجات فارغة أو غير موجودة")
            confidence = 0.0
            suggestions.append("يرجى توفير مخرجات من النظام")
            return ValidationResult(
                is_valid=False,
                confidence=confidence,
                issues=issues,
                suggestions=suggestions,
            )

        # التحقق من المعايير المتوقعة
        if expected_criteria:
            for criterion in expected_criteria:
                criterion_lower = criterion.lower()
                output_lower = output.lower()

                # فحص بسيط للكلمات المفتاحية
                if criterion_lower not in output_lower:
                    issues.append(f"المخرجات لا تحتوي على: {criterion}")
                    confidence -= 0.2
                    suggestions.append(f"أضف معلومات حول: {criterion}")

        confidence = max(0.0, confidence)

        return ValidationResult(
            is_valid=len(issues) == 0,
            confidence=confidence,
            issues=issues,
            suggestions=suggestions,
        )

    # ========================================================================
    # إدارة جلسات الوكلاء
    # ========================================================================

    async def register_agent_session(
        self,
        agent_id: str,
        session_data: dict[str, Any],
    ) -> None:
        """تسجيل جلسة وكيل جديد."""
        self._agent_sessions[agent_id] = {
            **session_data,
            "registered_at": datetime.now(UTC).isoformat(),
            "last_activity": datetime.now(UTC).isoformat(),
        }
        log.info("تم تسجيل جلسة وكيل: %s", agent_id)

    async def update_agent_activity(self, agent_id: str) -> None:
        """تحديث وقت النشاط الأخير للوكيل."""
        if agent_id in self._agent_sessions:
            self._agent_sessions[agent_id]["last_activity"] = datetime.now(UTC).isoformat()

    async def get_agent_session(self, agent_id: str) -> dict[str, Any] | None:
        """الحصول على بيانات جلسة وكيل."""
        return self._agent_sessions.get(agent_id)

    # ========================================================================
    # تشغيل وإيقاف المحرك
    # ========================================================================

    async def start(self) -> None:
        """بدء محرك التكامل."""
        if self._integration_running:
            log.warning("محرك التكامل يعمل بالفعل")
            return

        self._integration_running = True
        self._event_processor_task = asyncio.create_task(self._event_processor_loop())
        log.info("تم بدء محرك التكامل بين الوكيل والنظام")

    async def stop(self) -> None:
        """إيقاف محرك التكامل."""
        self._integration_running = False
        if self._event_processor_task:
            self._event_processor_task.cancel()
            try:
                await self._event_processor_task
            except asyncio.CancelledError:
                pass
        log.info("تم إيقاف محرك التكامل بين الوكيل والنظام")

    async def _event_processor_loop(self) -> None:
        """حلقة معالجة الأحداث."""
        while self._integration_running:
            try:
                # معالجة الأحداث غير المؤكدة
                unacknowledged = await self.get_unacknowledged_events()
                for event in unacknowledged:
                    log.debug("معالجة حدث نظام: %s (%s)", event.id, event.event_type)
                    # في النظام الحقيقي، يمكن إرسال الحدث للوكيل هنا
                    await self.acknowledge_event(event.id)

                await asyncio.sleep(2)  # فحص كل ثانيتين

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("خطأ في معالج الأحداث: %s", exc)
                await asyncio.sleep(5)

    def get_integration_status(self) -> dict[str, Any]:
        """الحصول على حالة محرك التكامل."""
        return {
            "running": self._integration_running,
            "registered_agents": len(self._agent_sessions),
            "pending_actions": sum(
                1 for a in self._system_actions.values()
                if a.status == "pending"
            ),
            "completed_actions": sum(
                1 for a in self._system_actions.values()
                if a.status == "completed"
            ),
            "failed_actions": sum(
                1 for a in self._system_actions.values()
                if a.status == "failed"
            ),
            "unacknowledged_events": sum(
                1 for e in self._system_events if not e.acknowledged
            ),
        }
