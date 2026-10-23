"""Smart Prompt Scheduler - محرك الجدولة الذكي للبرومبتات والمهام.

يتيح جدولة البرومبتات والمهام للتنفيذ فوراً، في وقت محدد، أو بشكل متكرر،
مع دعم الأولويات والاعتمادات بين المهام.
"""

from __future__ import annotations

import asyncio
import heapq
import inspect
import logging
import uuid
from dataclasses import dataclass, field, replace
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

    def __lt__(self, other: ScheduledPrompt) -> bool:
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
                if inspect.iscoroutinefunction(handler):
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

        for _condition_type, condition_value in conditions.items():
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
        self._wake = asyncio.Event()

    async def enqueue(self, task: ScheduledPrompt, *, wake: bool = True) -> str:
        """إضافة مهمة إلى قائمة الانتظار.

        ``wake=False`` is used when the scheduler re-queues a task whose
        dependencies or conditions are not met yet: waking itself immediately
        would turn an idle scheduler into a busy loop.
        """
        async with self._lock:
            self._counter += 1
            # دفع task مع الكاونتر لكسر التعادل
            heapq.heappush(self._heap, task)
            self._task_map[task.id] = task
            if wake:
                self._wake.set()
            log.debug("تمت إضافة المهمة %s إلى قائمة الانتظار (الأولوية: %s)", task.id, task.priority.name)
            return task.id

    async def dequeue(self) -> ScheduledPrompt | None:
        """Return the highest-priority due task without ever waiting.

        A queue whose head is scheduled for the future must return ``None``
        immediately. The previous implementation popped and re-pushed that head
        in a 0.1s loop while holding the queue lock, so one future task pinned a
        CPU core and blocked every enqueue for as long as its delay lasted.
        """
        deferred: list[ScheduledPrompt] = []
        due: list[ScheduledPrompt] = []
        async with self._lock:
            while self._heap:
                task = heapq.heappop(self._heap)
                if task.status != "pending":
                    # Cancelled or already executed: drop it for good.
                    self._task_map.pop(task.id, None)
                elif task.is_due():
                    due.append(task)
                else:
                    deferred.append(task)
            for task in deferred:
                heapq.heappush(self._heap, task)
            if not due:
                return None
            chosen = due[0]  # heap order already picked the best due task
            for task in due[1:]:
                heapq.heappush(self._heap, task)
            self._task_map.pop(chosen.id, None)
            return chosen

    async def next_due_in(self, now: datetime | None = None) -> float | None:
        """Seconds until the next pending task becomes due, or None if none is pending."""
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        async with self._lock:
            delay: float | None = None
            for task in self._heap:
                if task.status != "pending":
                    continue
                moments = [
                    moment
                    for moment in (task.execute_at, task.next_execution)
                    if moment is not None
                ]
                if not moments:
                    return 0.0
                ready_at = max(moments)
                if ready_at.tzinfo is None:
                    ready_at = ready_at.replace(tzinfo=UTC)
                remaining = (ready_at - current).total_seconds()
                if remaining <= 0:
                    return 0.0
                delay = remaining if delay is None else min(delay, remaining)
            return delay

    async def wait_for_work(self, timeout: float, *, clear: bool = True) -> bool:
        """Sleep until an enqueue wakes the queue or ``timeout`` elapses."""
        if clear:
            self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.01, float(timeout)))
        except TimeoutError:
            return False
        return True

    def reset_wakeup(self) -> None:
        """Clear a pending wake-up before a scheduler scans for work."""
        self._wake.clear()

    def wake(self) -> None:
        """Wake a scheduler waiting in :meth:`wait_for_work`."""
        self._wake.set()

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
        idle_wait_seconds: float = 30.0,
    ) -> None:
        self.max_concurrent_tasks = max_concurrent_tasks
        self.task_timeout_seconds = task_timeout_seconds
        # An idle scheduler has nothing to do until a task is enqueued or the
        # nearest scheduled task becomes due; checking once a second forever is
        # wasted wakeups on a small host.
        self.idle_wait_seconds = max(0.25, min(float(idle_wait_seconds), 300.0))
        self.queue = PriorityTaskQueue()
        self.condition_engine = ConditionEngine()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._running_prompts: dict[str, ScheduledPrompt] = {}
        self._finished_tasks: dict[str, ScheduledPrompt] = {}
        self._task_results: dict[str, str] = {}
        self._executor: Any = None
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
        task = (
            await self.queue.get_task(task_id)
            or self._running_prompts.get(task_id)
            or self._finished_tasks.get(task_id)
        )
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
        """إلغاء مهمة مجدولة أو جارية."""
        running = self._running_prompts.pop(task_id, None)
        if running is not None:
            running.status = "cancelled"
            runner = self._running_tasks.pop(task_id, None)
            if runner is not None:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)
            return True
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
        """إيقاف المجدول وإنهاء المهام الجارية التي أطلقها."""
        self._scheduler_running = False
        self.queue.wake()
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        running, self._running_tasks = self._running_tasks, {}
        self._running_prompts.clear()
        for task in running.values():
            task.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
        log.info("تم إيقاف المجدول الذكي")

    async def _scheduler_loop(self) -> None:
        """حلقة الجدولة الرئيسية."""
        while self._scheduler_running:
            try:
                blocked = False
                self.queue.reset_wakeup()
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
                                self._launch_task(task)
                            else:
                                # إعادة المهمة إلى القائمة إذا لم تتحقق الظروف
                                blocked = True
                                await self.queue.enqueue(task, wake=False)
                        else:
                            # إعادة المهمة إذا لم تكتمل الاعتمادات
                            blocked = True
                            await self.queue.enqueue(task, wake=False)

                delay = await self.queue.next_due_in()
                if len(self._running_tasks) >= self.max_concurrent_tasks:
                    # Due work is waiting but every slot is busy; completion
                    # wakes this loop through the done callback.
                    wait = self.idle_wait_seconds
                elif blocked:
                    # A deferred task stays due, so waiting on its due time would
                    # spin. One second preserves the previous retry cadence.
                    wait = 1.0
                elif delay is None:
                    wait = self.idle_wait_seconds
                else:
                    wait = min(max(0.05, delay), self.idle_wait_seconds)
                await self.queue.wait_for_work(wait, clear=False)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("خطأ في حلقة الجدولة: %s", exc)
                await asyncio.sleep(5)

    def _launch_task(self, task: ScheduledPrompt) -> None:
        """Run one due task concurrently without blocking the scheduler loop."""
        runner = asyncio.create_task(self._run_with_timeout(task))
        self._running_tasks[task.id] = runner
        self._running_prompts[task.id] = task
        # The loop must wake as soon as a task finishes so it can re-check
        # capacity and requeue recurring work without polling.
        runner.add_done_callback(lambda _: self.queue.wake())

    async def _run_with_timeout(self, task: ScheduledPrompt) -> str:
        """Apply ``task_timeout_seconds`` to the configured executor."""
        return await asyncio.wait_for(
            self._execute_task(task), timeout=self.task_timeout_seconds
        )

    async def _check_running_tasks(self) -> None:
        """فحص المهام الجارية وتحديث حالاتها."""
        done_tasks = [
            task_id
            for task_id, runner in self._running_tasks.items()
            if runner.done()
        ]

        for task_id in done_tasks:
            runner = self._running_tasks.pop(task_id)
            task = self._running_prompts.pop(task_id, None)
            try:
                result = await runner
                self._remember_result(task_id, result)
                if task is not None:
                    task.status = "completed"
                    task.result = result
                    self._remember_finished(task)

                    # إذا كانت متكررة، جدولة المرة التالية
                    if task.should_recur():
                        next_exec = task.calculate_next_execution()
                        if next_exec:
                            task.next_execution = next_exec
                            task.status = "pending"
                            await self.queue.enqueue(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                self._remember_result(task_id, message)
                if task is not None:
                    task.status = "failed"
                    task.error = message
                    self._remember_finished(task)

    def _remember_finished(self, task: ScheduledPrompt) -> None:
        """Keep a bounded completion history for dependency checks and status."""
        self._finished_tasks[task.id] = replace(task)
        while len(self._finished_tasks) > 500:
            self._finished_tasks.pop(next(iter(self._finished_tasks)), None)

    def _remember_result(self, task_id: str, result: str) -> None:
        """Keep recent results bounded; the scheduler may run for months."""
        self._task_results[task_id] = result
        while len(self._task_results) > 500:
            self._task_results.pop(next(iter(self._task_results)), None)

    async def _check_dependencies(self, task: ScheduledPrompt) -> bool:
        """التحقق من اكتمال الاعتمادات."""
        for dep_id in task.dependencies:
            dep = self._finished_tasks.get(dep_id) or await self.queue.get_task(dep_id)
            if dep is None or dep.status != "completed":
                return False
        return True

    async def _execute_task(self, task: ScheduledPrompt) -> str:
        """Execute one task through the registered executor or the demo path."""
        task.status = "running"
        task.execution_count += 1

        executor = self._executor
        if executor is None:
            # No executor configured: keep the historical simulated behaviour.
            await asyncio.sleep(0.1)
            result = f"تم تنفيذ المهمة {task.id} بنجاح (النوع: {task.schedule_type.value})"
        else:
            outcome = executor(task)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            result = str(outcome)

        task.status = "completed"
        task.result = result
        return result

    def register_executor(self, executor: Any) -> None:
        """تسجيل منفذ مخصص للمهام."""
        self._executor = executor
