"""Task Memory - ذاكرة المهام الطويلة.

توفر ذاكرة طويلة الأمد للمهام المتعددة الجلسات، مما يسمح باستعادة
حالة المهمة بعد إعادة تشغيل النظام أو انقطاع الاتصال.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("opencode_bridge.context.task_memory")


@dataclass
class TaskEvent:
    """حدث واحد في سجل المهمة."""

    id: str
    task_id: str
    event_type: str  # created, started, paused, resumed, completed, failed
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = field(default_factory=dict)
    actor: str | None = None  # من قام بالحدث (user, agent, system)


@dataclass
class TaskContext:
    """سياق المهمة مع جميع بياناتها الوصفية."""

    task_id: str
    title: str
    description: str
    status: str  # planned, running, paused, completed, failed, cancelled
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    owner_id: str | None = None  # معرف مستخدم تيليجرام
    agent_id: str | None = None  # معرف الوكيل المنفذ
    current_subtask: str | None = None  # المهمة الفرعية الحالية
    variables: dict[str, Any] = field(default_factory=dict)  # متغيرات المهمة
    artifacts: dict[str, str] = field(default_factory=dict)  # مخرجات المهمة (ملفات، نتائج)
    tags: list[str] = field(default_factory=list)  # علامات للتصنيف والبحث
    priority: int = 3  # 1 = الأعلى، 5 = الأقل


class TaskMemory:
    """ذاكرة المهام الطويلة.

    تخزين واستعادة حالات المهام في قاعدة بيانات محلية (JSON أو SQLite)،
    مما يسمح باستعادة المهام بعد إعادة تشغيل النظام أو انقطاع الاتصال.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or Path(__file__).parent / "task_memory"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, TaskContext] = {}
        self._events: dict[str, list[TaskEvent]] = {}  # task_id -> events
        self._lock = asyncio.Lock()
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        """تحميل البيانات من القرص إذا لم يتم التحميل بعد."""
        if self._loaded:
            return
        await self._load_from_disk()
        self._loaded = True

    async def _load_from_disk(self) -> None:
        """تحميل المهام والأحداث من ملفات JSON على القرص."""
        tasks_file = self.storage_path / "tasks.json"
        events_file = self.storage_path / "events.json"

        if tasks_file.exists():
            try:
                with tasks_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                for task_id, task_data in data.items():
                    self._tasks[task_id] = TaskContext(
                        task_id=task_data["task_id"],
                        title=task_data["title"],
                        description=task_data["description"],
                        status=task_data["status"],
                        created_at=datetime.fromisoformat(task_data["created_at"]),
                        updated_at=datetime.fromisoformat(task_data["updated_at"]),
                        completed_at=(
                            datetime.fromisoformat(task_data["completed_at"])
                            if task_data.get("completed_at")
                            else None
                        ),
                        owner_id=task_data.get("owner_id"),
                        agent_id=task_data.get("agent_id"),
                        current_subtask=task_data.get("current_subtask"),
                        variables=task_data.get("variables", {}),
                        artifacts=task_data.get("artifacts", {}),
                        tags=task_data.get("tags", []),
                        priority=task_data.get("priority", 3),
                    )
                log.info("تم تحميل %d مهمة من الذاكرة", len(self._tasks))
            except Exception as exc:
                log.warning("فشل تحميل المهام من القرص: %s", exc)

        if events_file.exists():
            try:
                with events_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                for task_id, events_data in data.items():
                    self._events[task_id] = [
                        TaskEvent(
                            id=e["id"],
                            task_id=e["task_id"],
                            event_type=e["event_type"],
                            timestamp=datetime.fromisoformat(e["timestamp"]),
                            data=e.get("data", {}),
                            actor=e.get("actor"),
                        )
                        for e in events_data
                    ]
                log.info("تم تحميل أحداث مهام من القرص")
            except Exception as exc:
                log.warning("فشل تحميل الأحداث من القرص: %s", exc)

    async def _save_to_disk(self) -> None:
        """حفظ المهام والأحداث إلى ملفات JSON على القرص."""
        tasks_file = self.storage_path / "tasks.json"
        events_file = self.storage_path / "events.json"

        try:
            tasks_data = {}
            for task_id, task in self._tasks.items():
                tasks_data[task_id] = {
                    "task_id": task.task_id,
                    "title": task.title,
                    "description": task.description,
                    "status": task.status,
                    "created_at": task.created_at.isoformat(),
                    "updated_at": task.updated_at.isoformat(),
                    "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                    "owner_id": task.owner_id,
                    "agent_id": task.agent_id,
                    "current_subtask": task.current_subtask,
                    "variables": task.variables,
                    "artifacts": task.artifacts,
                    "tags": task.tags,
                    "priority": task.priority,
                }
            with tasks_file.open("w", encoding="utf-8") as f:
                json.dump(tasks_data, f, ensure_ascii=False, indent=2)

            events_data = {}
            for task_id, events in self._events.items():
                events_data[task_id] = [
                    {
                        "id": e.id,
                        "task_id": e.task_id,
                        "event_type": e.event_type,
                        "timestamp": e.timestamp.isoformat(),
                        "data": e.data,
                        "actor": e.actor,
                    }
                    for e in events
                ]
            with events_file.open("w", encoding="utf-8") as f:
                json.dump(events_data, f, ensure_ascii=False, indent=2)

        except Exception as exc:
            log.error("فشل حفظ المهام والأحداث إلى القرص: %s", exc)

    async def create_task(
        self,
        task_id: str,
        title: str,
        description: str,
        owner_id: str | None = None,
        agent_id: str | None = None,
        tags: list[str] | None = None,
        priority: int = 3,
    ) -> TaskContext:
        """إنشاء مهمة جديدة في الذاكرة."""
        async with self._lock:
            await self._ensure_loaded()

            task = TaskContext(
                task_id=task_id,
                title=title,
                description=description,
                status="planned",
                owner_id=owner_id,
                agent_id=agent_id,
                tags=tags or [],
                priority=priority,
            )
            self._tasks[task_id] = task
            await self._add_event(
                task_id=task_id,
                event_type="created",
                data={"title": title, "description": description},
                actor=owner_id,
            )
            await self._save_to_disk()
            log.info("تم إنشاء مهمة جديدة: %s (%s)", task_id, title)
            return task

    async def get_task(self, task_id: str) -> TaskContext | None:
        """الحصول على مهمة بمعرفها."""
        async with self._lock:
            await self._ensure_loaded()
            return self._tasks.get(task_id)

    async def update_task(
        self,
        task_id: str,
        **updates: Any,
    ) -> bool:
        """تحديث بيانات مهمة."""
        async with self._lock:
            await self._ensure_loaded()

            task = self._tasks.get(task_id)
            if task is None:
                return False

            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            task.updated_at = datetime.now(UTC)

            await self._save_to_disk()
            log.info("تم تحديث المهمة: %s", task_id)
            return True

    async def add_event(
        self,
        task_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> str:
        """إضافة حدث إلى سجل مهمة."""
        async with self._lock:
            await self._ensure_loaded()
            return await self._add_event(
                task_id=task_id,
                event_type=event_type,
                data=data or {},
                actor=actor,
            )

    async def _add_event(
        self,
        task_id: str,
        event_type: str,
        data: dict[str, Any],
        actor: str | None = None,
    ) -> str:
        """إضافة حدث داخلياً (بافتراض أن القفل مُ acquis)."""
        import uuid

        event_id = str(uuid.uuid4())
        event = TaskEvent(
            id=event_id,
            task_id=task_id,
            event_type=event_type,
            timestamp=datetime.now(UTC),
            data=data,
            actor=actor,
        )

        if task_id not in self._events:
            self._events[task_id] = []
        self._events[task_id].append(event)

        # حفظ الأحداث دورياً (كل 10 أحداث)
        if len(self._events.get(task_id, [])) % 10 == 0:
            await self._save_to_disk()

        return event_id

    async def get_task_events(
        self,
        task_id: str,
        limit: int = 50,
    ) -> list[TaskEvent]:
        """الحصول على أحداث مهمة."""
        async with self._lock:
            await self._ensure_loaded()
            events = self._events.get(task_id, [])
            return events[-limit:] if limit else events

    async def list_tasks(
        self,
        owner_id: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[TaskContext]:
        """قائمة المهام مع تصفية اختيارية."""
        async with self._lock:
            await self._ensure_loaded()

            tasks = list(self._tasks.values())

            if owner_id:
                tasks = [t for t in tasks if t.owner_id == owner_id]
            if status:
                tasks = [t for t in tasks if t.status == status]
            if tag:
                tasks = [t for t in tasks if tag in t.tags]

            # ترتيب حسب وقت الإنشاء (الأحدث أولاً)
            tasks.sort(key=lambda t: t.created_at, reverse=True)
            return tasks[:limit]

    async def delete_task(self, task_id: str) -> bool:
        """حذف مهمة وأحداثها."""
        async with self._lock:
            await self._ensure_loaded()

            if task_id not in self._tasks:
                return False

            del self._tasks[task_id]
            self._events.pop(task_id, None)
            await self._save_to_disk()
            log.info("تم حذف المهمة: %s", task_id)
            return True

    async def search_tasks(
        self,
        query: str,
        owner_id: str | None = None,
        limit: int = 50,
    ) -> list[TaskContext]:
        """البحث في المهام بالنص (العنوان، الوصف، العلامات)."""
        async with self._lock:
            await self._ensure_loaded()

            query_lower = query.lower()
            results = []

            for task in self._tasks.values():
                if owner_id and task.owner_id != owner_id:
                    continue

                score = 0
                if query_lower in task.title.lower():
                    score += 3
                if query_lower in task.description.lower():
                    score += 2
                if any(query_lower in tag.lower() for tag in task.tags):
                    score += 1

                if score > 0:
                    results.append((score, task))

            # ترتيب حسب النتيجة (الأعلى أولاً)
            results.sort(key=lambda x: x[0], reverse=True)
            return [task for _, task in results[:limit]]

    async def get_task_variables(self, task_id: str) -> dict[str, Any]:
        """الحصول على متغيرات مهمة."""
        async with self._lock:
            await self._ensure_loaded()
            task = self._tasks.get(task_id)
            return task.variables if task else {}

    async def set_task_variable(
        self,
        task_id: str,
        key: str,
        value: Any,
    ) -> bool:
        """تعيين متغير مهمة."""
        async with self._lock:
            await self._ensure_loaded()
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.variables[key] = value
            task.updated_at = datetime.now(UTC)
            await self._save_to_disk()
            return True

    async def get_task_artifact(self, task_id: str, artifact_name: str) -> str | None:
        """الحصول على إخراج مهمة (ملف أو نتيجة)."""
        async with self._lock:
            await self._ensure_loaded()
            task = self._tasks.get(task_id)
            return task.artifacts.get(artifact_name) if task else None

    async def set_task_artifact(
        self,
        task_id: str,
        artifact_name: str,
        value: str,
    ) -> bool:
        """تعيين إخراج مهمة."""
        async with self._lock:
            await self._ensure_loaded()
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.artifacts[artifact_name] = value
            task.updated_at = datetime.now(UTC)
            await self._save_to_disk()
            return True

    async def get_task_statistics(self) -> dict[str, Any]:
        """الحصول على إحصائيات المهام."""
        async with self._lock:
            await self._ensure_loaded()

            total = len(self._tasks)
            by_status: dict[str, int] = {}
            for task in self._tasks.values():
                by_status[task.status] = by_status.get(task.status, 0) + 1

            return {
                "total_tasks": total,
                "by_status": by_status,
                "total_events": sum(len(e) for e in self._events.values()),
            }

    async def close(self) -> None:
        """إغلاق ذاكرة المهام وحفظ البيانات."""
        async with self._lock:
            await self._save_to_disk()
            log.info("تم إغلاق ذاكرة المهام وحفظ البيانات")
