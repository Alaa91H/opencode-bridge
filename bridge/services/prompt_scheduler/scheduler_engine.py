"""Smart Prompt Scheduler - محرك الجدولة الذكي للبرومبتات والمهام.

يتيح جدولة البرومبتات والمهام للتنفيذ فوراً، في وقت محدد، أو بشكل متكرر،
مع دعم الأولويات والاعتمادات بين المهام.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

log = logging.getLogger("opencode_bridge.prompt_scheduler.scheduler_engine")


class ScheduleType(Enum):
    """أنواع الجدولة المدعومة."""

    IMMEDIATE = "immediate"  # تنفيذ فوري
    DELAYED = "delayed"  # تنفيذ في وقت محدد
    RECURRING = "recurring"  # تنفيذ متكرر بفاصل زمني
    CONDITIONAL = "conditional"  # تنفيذ عند تحقق شرط معين


class Priority(Enum):
    """مستويات الأولوية للمهام."""

    CRITICAL = 1  # حرج - تنفيذ فوراً مهما كان
    HIGH = 2  # عالي - أولوية عالية
    NORMAL = 3  # عادي - الأولوية الافتراضية
    LOW = 4  # منخفض - تنفيذ عند توفر الموارد
    BACKGROUND = 5  # خلفي - تنفيذ في أوقات الخمول


@dataclass
class ScheduledPrompt:
    """تمثيل البرومبت المجدول مع جميع بيانات تنفيذه."""

    id: str
    prompt: str
    schedule_type: ScheduleType
    priority: Priority
    execute_at: datetime | None = None  # للتنفيذ في وقت محدد (DELAYED)
    interval: str | None = None  # للفاصل الزمني (RECURRING) مثل "1h", "1d", "1w"
    dependencies: list[str] = field(default_factory=list)  # معرفات مهام سابقة يجب أن تكتمل أولاً
    conditions: dict[str, Any] = field(default_factory=dict)  # شروط تشغيل
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "pending"  # pending, running, completed, failed, cancelled
    result: str | None = None  # نتيجة التنفيذ
    error: str | None = None  # رسالة الخطأ إذا فشل
    next_execution: datetime | None = None  # المرة التالية للتنفيذ (للمتكرر)
    execution_count: int = 0  # عدد مرات التنفيذ
    max_executions: int | None = None  # الحد الأقصى لعدد مرات التنفيذ (للمتكرر)
    metadata: dict[str, Any] = field(default_factory=dict)  # بيانات إضافية

    def __lt__(self, other: "ScheduledPrompt") -> bool:
        """مقارنة للترتيب في قائمة الانتظار based على الأولوية والوقت."""
        if self.priority.value != other.priority.value:
            return self.priority.value < other.priority.value
        if self.execute_at and other.execute_at:
            return self.execute_at < other.execute_at
        return self.created_at < other.created_at

    def is_due(self, now: datetime | None = None) -> bool:
        """التحقق من إذا كان الوقت قد حان للتنفيذ."""
        if now is None:
            now = datetime.now(UTC)
        if self.status != "pending":
            return False
        if self.execute_at and now < self.execute_at:
            return False
        if self.next_execution and now < self.next_execution:
            return False
        return True

    def should_recur(self) -> bool:
        """التحقق من إذا كانت المهمة متكررة ولا تزال ضمن الحد الأقصى."""
        if self.schedule_type != ScheduleType.RECURRING:
            return False
        if self.max_executions is not None and self.execution_count >= self.max_executions:
            return False
        return True

    def calculate_next_execution(self) -> datetime | None:
        """حساب وقت التنفيذ التالي للمهام المتكررة."""
        if self.schedule_type != ScheduleType.RECURRING or not self.interval:
            return None

        now = datetime.now(UTC)
        base = self.next_execution or self.created_at

        try:
            if self.interval.endswith("s"):
                seconds = int(self.interval[:-1])
                delta = timedelta(seconds=seconds)
            elif self.interval.endswith("m"):
                minutes = int(self.interval[:-1])
                delta = timedelta(minutes=minutes)
            elif self.interval.endswith("h"):
                hours = int(self.interval[:-1])
                delta = timedelta(hours=hours)
            elif self.interval.endswith("d"):
                days = int(self.interval[:-1])
                delta = timedelta(days=days)
            elif self.interval.endswith("w"):
                weeks = int(self.interval[:-1])
                delta = timedelta(weeks=weeks)
            else:
                log.warning("فاصل زمني غير معروف: %s", self.interval)
                return None

            return base + delta
        except (ValueError, IndexError) as exc:
            log.warning("فشل في تحليل الفاصل الزمني '%s': %s", self.interval, exc)
            return None


class ConditionEngine:
    """محرك تقييم ظروف التشغيل للمهام المجدولة.

    يدعم ظروف مثل:
    - after_build_success: تنفيذ بعد نجاح عملية البناء
    - resource_available: تنفيذ عند توفر موارد النظام
    - time_based: تنفيذ في وقت معين من اليوم
    - custom: شروط مخصصة حسب الحاجة
    """

    def __init__(self) -> None:
        self._condition_handlers: dict[str, Any] = {}

    def register_handler(self, condition_type: str, handler: Any) -> None:
        """تسجيل معالج لشرط مخصص."""
        self._condition_handlers[condition_type] = handler

    async def evaluate_condition(
        self,
        condition: str,
        context: dict[str, Any],
    ) -> bool:
        """تقييم شرط واحد."""
        try:
            if condition == "always":
                return True
            if condition == "never":
                return False
            if condition == "after_build_success":
                return context.get("build_success", False)
            if condition == "resource_available":
                return context.get("resources_available", True)
            if condition == "time_based":
                # التحقق من الوقت الحالي
                now = datetime.now(UTC)
                return now.hour >= 6 and now.hour < 22  # 6 صباحاً إلى 10 مساءً
            if condition in self._condition_handlers:
                handler = self._condition_handlers[condition]
                if asyncio.iscoroutinefunction(handler):
                    return await handler(context)
                return handler(context)
            return True  # افتراضياً، إذا لم نRecognize الشرط، نعتبره محققاً
        except Exception as exc:
            log.warning("فشل في تقييم الشرط '%s': %s", condition, exc)
            return False

    async def evaluate_all_conditions(
        self,
        conditions: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        """تقييم جميع الظروف والمُ RETURN True إذا كانت جميعها محققة."""
        if not conditions:
            return True

        for condition_type, condition_value in conditions.items():
            if isinstance(condition_value, bool):
                if not condition_value:
                    return False
            elif isinstance(condition_value, str):
                if not await self.evaluate_condition(condition_value, context):
                    return False
            elif isinstance(condition_value, dict):
                # شرط معقد مع عوامل
                if condition_value.get("type") == "all":
                    conditions_list = condition_value.get("conditions", [])
                    for c in conditions_list:
                        if not await self.evaluate_condition(c, context):
                            return False
                elif condition_value.get("type") == "any":
                    conditions_list = condition_value.get("conditions", [])
                    if not any(await self.evaluate_condition(c, context) for c in conditions_list):
                        return False
            else:
                # غير معروف، نعتبره محققاً
                pass

        return True


class PriorityTaskQueue:
    """قائمة انتظار ذات أولويات للمهام المجدولة.

    تستخدم كومة (heap) لترتيب المهام بناءً على الأولوية والوقت.
    """

    def __init__(self) -> None:
        self._heap: list[ScheduledPrompt] = []
        self._counter = 0  # لكسر التعادل في الأولوية نفسها
        self._lock = asyncio.Lock()
        self._task_map: dict[str, ScheduledPrompt] = {}  # id -> task للبحث السريع

    async def enqueue(self, task: ScheduledPrompt) -> str:
        """إضافة مهمة إلى قائمة الانتظار."""
        async with self._lock:
            self._counter += 1
            # دفع task مع الكاونتر لكسر التعادل
            heapq.heappush(self._heap, task)
            self._task_map[task.id] = task
            log.debug("تمت إضافة المهمة %s إلى قائمة الانتظار (الأولوية: %s)", task.id, task.priority.name)
            return task.id

    async def dequeue(self) -> ScheduledPrompt | None:
        """إرجاع أعلى مهمة أولوية وجاهزة للتنفيذ."""
        async with self._lock:
            while self._heap:
                task = heapq.heappop(self._heap)
                if task.is_due():
                    # إزالة من الخريطة
                    self._task_map.pop(task.id, None)
                    return task
                else:
                    # إعادة دفع المهمة إذا لم يحن وقتها بعد
                    heapq.heappush(self._heap, task)
                    await asyncio.sleep(0.1)  # تجنب الدوران السريع
            return None

    async def peek(self) -> ScheduledPrompt | None:
        """عرض أعلى مهمة دون إزالتها من القائمة."""
        async with self._lock:
            # البحث عن أول مهمة جاهزة
            for task in self._heap:
                if task.is_due():
                    return task
            return None

    async def get_task(self, task_id: str) -> ScheduledPrompt | None:
        """الحصول على مهمة بمعرفها."""
        async with self._lock:
            return self._task_map.get(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """إلغاء مهمة."""
        async with self._lock:
            task = self._task_map.pop(task_id, None)
            if task:
                task.status = "cancelled"
                # ملاحظة: المهمة ستبقى في الكومة لكنها ستتجاهل عند السحب
                log.info("تم إلغاء المهمة %s", task_id)
                return True
            return False

    async def get_status(self) -> dict[str, Any]:
        """الحصول على حالة قائمة الانتظار."""
        async with self._lock:
            pending = sum(1 for t in self._heap if t.status == "pending")
            running = sum(1 for t in self._heap if t.status == "running")
            completed = sum(1 for t in self._heap if t.status == "completed")
            failed = sum(1 for t in self._heap if t.status == "failed")
            cancelled = sum(1 for t in self._heap if t.status == "cancelled")

            return {
                "total": len(self._heap),
                "pending": pending,
                "running": running,
                "completed": completed,
                "failed": failed,
                "cancelled": cancelled,
                "next_due": self._get_next_due_time(),
            }

    def _get_next_due_time(self) -> str | None:
        """الحصول على وقت nearest task due."""
        now = datetime.now(UTC)
        due_tasks = [t for t in self._heap if t.is_due(now)]
        if not due_tasks:
            return None
        earliest = min(due_tasks, key=lambda t: t.execute_at or t.created_at)
        return (earliest.execute_at or earliest.created_at).isoformat()

    async def clear_completed(self) -> int:
        """مسح المهام المكتملة من القائمة."""
        async with self._lock:
            original_count = len(self._heap)
            self._heap = [t for t in self._heap if t.status not in ("completed", "cancelled")]
            heapq.heapify(self._heap)
            removed = original_count - len(self._heap)
            # إعادة بناء الخريطة
            self._task_map = {t.id: t for t in self._heap}
            return removed


class SmartPromptScheduler:
    """المحرك الرئيسي للجدولة الذكية للبرومبتات والمهام.

    يدمج بين محرك الجدولة، محرك الظروف، وقائمة الانتظار ذات الأولويات
    لتوفير نظام متكامل لإدارة المهام المجدولة.
    """

    def __init__(
        self,
        max_concurrent_tasks: int = 5,
        task_timeout_seconds: int = 3600,
    ) -> None:
        self.max_concurrent_tasks = max_concurrent_tasks
        self.task_timeout_seconds = task_timeout_seconds
        self.queue = PriorityTaskQueue()
        self.condition_engine = ConditionEngine()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._task_results: dict[str, str] = {}
        self._scheduler_running = False
        self._scheduler_task: asyncio.Task | None = None

    async def schedule_prompt(
        self,
        prompt: str,
        schedule_type: ScheduleType = ScheduleType.IMMEDIATE,
        *,
        execute_at: datetime | None = None,
        interval: str | None = None,
        priority: Priority = Priority.NORMAL,
        dependencies: list[str] | None = None,
        conditions: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        max_executions: int | None = None,
    ) -> str:
        """جدولة برومبت للتنفيذ.

        Args:
            prompt: نص البرومبت للتنفيذ
            schedule_type: نوع الجدولة (فوري، محدد، متكرر، مشروط)
            execute_at: وقت التنفيذ المحدد (لـ DELAYED)
            interval: الفاصل الزمني (لـ RECURRING) مثل "1h", "1d", "1w"
            priority: مستوى الأولوية
            dependencies: معرفات مهام سابقة يجب أن تكتمل أولاً
            conditions: شروط تشغيل يجب أن تتحقق
            metadata: بيانات إضافية
            max_executions: الحد الأقصى لعدد مرات التنفيذ (للمتكرر)

        Returns:
            معرف المهمة المجدولة
        """
        task = ScheduledPrompt(
            id=str(uuid.uuid4()),
            prompt=prompt,
            schedule_type=schedule_type,
            priority=priority,
            execute_at=execute_at,
            interval=interval,
            dependencies=dependencies or [],
            conditions=conditions or {},
            metadata=metadata or {},
            max_executions=max_executions,
        )

        await self.queue.enqueue(task)
        log.info(
            "تمت جدولة المهمة %s (النوع: %s، الأولوية: %s، الوقت: %s)",
            task.id,
            schedule_type.value,
            priority.name,
            execute_at.isoformat() if execute_at else "فوري",
        )
        return task.id

    async def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """الحصول على حالة مهمة."""
        task = await self.queue.get_task(task_id)
        if task is None:
            return None

        result = {
            "id": task.id,
            "status": task.status,
            "priority": task.priority.name,
            "schedule_type": task.schedule_type.value,
            "created_at": task.created_at.isoformat(),
            "execute_at": task.execute_at.isoformat() if task.execute_at else None,
            "execution_count": task.execution_count,
            "error": task.error,
        }

        if task.result is not None:
            result["result"] = task.result

        return result

    async def cancel_task(self, task_id: str) -> bool:
        """إلغاء مهمة مجدولة."""
        return await self.queue.cancel_task(task_id)

    async def get_queue_status(self) -> dict[str, Any]:
        """الحصول على حالة قائمة الانتظار الكاملة."""
        queue_status = await self.queue.get_status()
        queue_status["running_tasks"] = len(self._running_tasks)
        queue_status["max_concurrent"] = self.max_concurrent_tasks
        return queue_status

    async def start_scheduler(self) -> None:
        """بدء دورة الجدولة المستمرة."""
        if self._scheduler_running:
            log.warning("المجدول يعمل بالفعل")
            return

        self._scheduler_running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        log.info("تم بدء المجدول الذكي")

    async def stop_scheduler(self) -> None:
        """إيقاف المجدول."""
        self._scheduler_running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        log.info("تم إيقاف المجدول الذكي")

    async def _scheduler_loop(self) -> None:
        """حلقة الجدولة الرئيسية."""
        while self._scheduler_running:
            try:
                # التحقق من المهام الجارية وإزالة المنتهية
                await self._check_running_tasks()

                # إذا كان هناك مكان لمهام جديدة، سحب مهمة من القائمة
                if len(self._running_tasks) < self.max_concurrent_tasks:
                    task = await self.queue.dequeue()
                    if task:
                        # التحقق من الاعتمادات
                        if await self._check_dependencies(task):
                            # التحقق من الظروف
                            context = {"build_success": True, "resources_available": True}
                            if await self.condition_engine.evaluate_all_conditions(
                                task.conditions, context
                            ):
                                await self._execute_task(task)
                            else:
                                # إعادة المهمة إلى القائمة إذا لم تتحقق الظروف
                                await self.queue.enqueue(task)
                        else:
                            # إعادة المهمة إذا لم تكتمل الاعتمادات
                            await self.queue.enqueue(task)

                await asyncio.sleep(1)  # فحص كل ثانية

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("خطأ في حلقة الجدولة: %s", exc)
                await asyncio.sleep(5)

    async def _check_running_tasks(self) -> None:
        """فحص المهام الجارية وتحديث حالاتها."""
        done_tasks = []
        for task_id, task in self._running_tasks.items():
            if task.done():
                done_tasks.append(task_id)

        for task_id in done_tasks:
            task = self._running_tasks.pop(task_id)
            try:
                result = await task
                self._task_results[task_id] = result
                # تحديث حالة المهمة في القائمة
                queue_task = await self.queue.get_task(task_id)
                if queue_task:
                    queue_task.status = "completed"
                    queue_task.result = result
                    queue_task.execution_count += 1

                    # إذا كانت متكررة، جدولة المرة التالية
                    if queue_task.should_recur():
                        next_exec = queue_task.calculate_next_execution()
                        if next_exec:
                            queue_task.next_execution = next_exec
                            queue_task.status = "pending"
                            await self.queue.enqueue(queue_task)
            except Exception as exc:
                self._task_results[task_id] = str(exc)
                queue_task = await self.queue.get_task(task_id)
                if queue_task:
                    queue_task.status = "failed"
                    queue_task.error = str(exc)

    async def _check_dependencies(self, task: ScheduledPrompt) -> bool:
        """التحقق من اكتمال الاعتمادات."""
        for dep_id in task.dependencies:
            dep_status = await self.queue.get_task(dep_id)
            if dep_status is None or dep_status.status != "completed":
                return False
        return True

    async def _execute_task(self, task: ScheduledPrompt) -> str:
        """تنفيذ مهمة واحدة (يتم overriding من قبل המערכת الخارجية)."""
        # هذه الدالة يتم overriding من قبل النظام الذي يستخدم المجدول
        # لتنفيذ المهمة فعلياً. هنا فقط نسجل البداية والنهاية.
        task.status = "running"
        task.execution_count += 1

        # محاكاة التنفيذ (في النظام الحقيقي، يتم استدعاء الوكيل هنا)
        await asyncio.sleep(0.1)  # محاكاة وقت التنفيذ

        result = f"تم تنفيذ المهمة {task.id} بنجاح (النوع: {task.schedule_type.value})"
        task.status = "completed"
        task.result = result

        return result

    def register_executor(self, executor: Any) -> None:
        """تسجيل منفذ مخصص للمهام."""
        self._executor = executor
