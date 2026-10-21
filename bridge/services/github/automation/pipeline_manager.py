"""نظام سير العمل التلقائي (Automation Pipelines) - إدارة سير العمل التلقائي للمشاريع.

يتيح هذا النظام إنشاء وتنفيذ سير عمل تلقائي للمهام المتكررة مثل:
- النشر التلقائي عند دمج طلبات السحب
- إنشاء إصدارات تلقائية عند دمج فروع الإصدار
- إغلاق القضايا تلقائياً عند دمج طلبات السحب المرتبطة
- إرسال إشعارات عند الأحداث المهمة
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from bridge.services.github.api import GitHubAPIClient
from bridge.services.github.issues import GitHubIssueManager
from bridge.services.github.pull_requests import GitHubPullRequestManager
from bridge.services.github.releases import GitHubReleaseManager
from bridge.services.github.repositories import GitHubRepositoryManager

log = logging.getLogger("opencode_bridge.github.automation")


class PipelineTrigger(Enum):
    """أنواع محفزات سير العمل التلقائي."""

    PULL_REQUEST_OPENED = "pull_request.opened"
    PULL_REQUEST_CLOSED = "pull_request.closed"
    PULL_REQUEST_MERGED = "pull_request.merged"
    ISSUE_OPENED = "issue.opened"
    ISSUE_CLOSED = "issue.closed"
    RELEASE_PUBLISHED = "release.published"
    BRANCH_CREATED = "branch.created"
    BRANCH_DELETED = "branch.deleted"
    PUSH_TO_BRANCH = "push.to_branch"
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class PipelineStepStatus(Enum):
    """حالات خطوات سير العمل التلقائي."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PipelineStep:
    """خطوة واحدة في سير العمل التلقائي."""

    id: str
    name: str
    description: str
    action_type: str  # create_issue, create_pr, create_release, send_notification, etc.
    parameters: dict[str, Any] = field(default_factory=dict)
    status: PipelineStepStatus = PipelineStepStatus.PENDING
    depends_on: list[str] = field(default_factory=list)  # معرفات خطوات سابقة
    result: Any = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class AutomationPipeline:
    """تعريف سير العمل التلقائي."""

    id: str
    name: str
    description: str
    trigger: PipelineTrigger
    owner: str
    repo: str
    steps: list[PipelineStep] = field(default_factory=list)
    is_enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_run: datetime | None = None
    run_count: int = 0
    success_count: int = 0
    failure_count: int = 0


@dataclass
class PipelineExecution:
    """تنفيذ واحد لسير العمل التلقائي."""

    id: str
    pipeline_id: str
    trigger_event: dict[str, Any]
    status: PipelineStepStatus = PipelineStepStatus.PENDING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    steps_results: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class AutomationPipelineManager:
    """مدير سير العمل التلقائي للمشاريع على GitHub.

    يتيح إنشاء وإدارة سير عمل تلقائي يستجيب للأحداث على GitHub
    وينفذ إجراءات تلقائية استجابة لهذه الأحداث.
    """

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client
        self.repo_manager = GitHubRepositoryManager(client)
        self.pr_manager = GitHubPullRequestManager(client)
        self.issue_manager = GitHubIssueManager(client)
        self.release_manager = GitHubReleaseManager(client)

        # تخزين خطوط السير في الذاكرة (في الإنتاج يمكن استخدام قاعدة بيانات)
        self._pipelines: dict[str, AutomationPipeline] = {}
        self._executions: dict[str, PipelineExecution] = {}
        self._lock = asyncio.Lock()

    async def create_pipeline(
        self,
        name: str,
        description: str,
        trigger: PipelineTrigger,
        owner: str,
        repo: str,
        steps: list[dict[str, Any]],
        is_enabled: bool = True,
    ) -> str:
        """إنشاء سير عمل تلقائي جديد.

        Args:
            name: اسم سير العمل
            description: وصف سير العمل
            trigger: نوع المحفز
            owner: مالك المستودع
            repo: اسم المستودع
            steps: خطوات السير العمل
            is_enabled: هل السير العمل مفعل

        Returns:
            معرف سير العمل الذي تم إنشاؤه
        """
        pipeline_id = str(uuid.uuid4())

        steps_objects = []
        for i, step_data in enumerate(steps):
            step = PipelineStep(
                id=step_data.get("id", f"step_{i}"),
                name=step_data.get("name", f"خطوة {i+1}"),
                description=step_data.get("description", ""),
                action_type=step_data.get("action_type", ""),
                parameters=step_data.get("parameters", {}),
                depends_on=step_data.get("depends_on", []),
            )
            steps_objects.append(step)

        pipeline = AutomationPipeline(
            id=pipeline_id,
            name=name,
            description=description,
            trigger=trigger,
            owner=owner,
            repo=repo,
            steps=steps_objects,
            is_enabled=is_enabled,
        )

        async with self._lock:
            self._pipelines[pipeline_id] = pipeline

        log.info(
            "تم إنشاء سير عمل تلقائي جديد: %s (المستودع: %s/%s, المحفز: %s)",
            name,
            owner,
            repo,
            trigger.value,
        )
        return pipeline_id

    async def get_pipeline(self, pipeline_id: str) -> AutomationPipeline | None:
        """الحصول على سير عمل بمعرفه."""
        async with self._lock:
            return self._pipelines.get(pipeline_id)

    async def list_pipelines(
        self,
        owner: str | None = None,
        repo: str | None = None,
        enabled_only: bool = False,
    ) -> list[AutomationPipeline]:
        """قائمة خطوط السير مع تصفية اختيارية."""
        async with self._lock:
            pipelines = list(self._pipelines.values())

            if owner:
                pipelines = [p for p in pipelines if p.owner == owner]
            if repo:
                pipelines = [p for p in pipelines if p.repo == repo]
            if enabled_only:
                pipelines = [p for p in pipelines if p.is_enabled]

            return pipelines

    async def update_pipeline(
        self,
        pipeline_id: str,
        **updates: Any,
    ) -> bool:
        """تحديث تكوين سير العمل."""
        async with self._lock:
            pipeline = self._pipelines.get(pipeline_id)
            if pipeline is None:
                return False

            for key, value in updates.items():
                if hasattr(pipeline, key):
                    setattr(pipeline, key, value)

            log.info("تم تحديث سير العمل: %s", pipeline_id)
            return True

    async def delete_pipeline(self, pipeline_id: str) -> bool:
        """حذف سير عمل."""
        async with self._lock:
            if pipeline_id not in self._pipelines:
                return False

            del self._pipelines[pipeline_id]
            log.info("تم حذف سير العمل: %s", pipeline_id)
            return True

    async def enable_pipeline(self, pipeline_id: str) -> bool:
        """تفعيل سير عمل."""
        return await self.update_pipeline(pipeline_id, is_enabled=True)

    async def disable_pipeline(self, pipeline_id: str) -> bool:
        """تعطيل سير عمل."""
        return await self.update_pipeline(pipeline_id, is_enabled=False)

    async def trigger_pipeline(
        self,
        pipeline_id: str,
        trigger_event: dict[str, Any] | None = None,
    ) -> str:
        """تشغيل سير عمل يدوياً أو استجابة لحدث."""
        async with self._lock:
            pipeline = self._pipelines.get(pipeline_id)
            if pipeline is None:
                raise ValueError(f"سير العمل غير موجود: {pipeline_id}")

            if not pipeline.is_enabled:
                raise ValueError(f"سير العمل معطل: {pipeline_id}")

        execution_id = str(uuid.uuid4())
        execution = PipelineExecution(
            id=execution_id,
            pipeline_id=pipeline_id,
            trigger_event=trigger_event or {},
        )

        async with self._lock:
            self._executions[execution_id] = execution

        # بدء التنفيذ في الخلفية
        _task = asyncio.create_task(self._run_pipeline(execution_id))  # noqa: RUF006

        log.info(
            "تم تشغيل سير العمل: %s (التنفيذ: %s)",
            pipeline_id,
            execution_id,
        )
        return execution_id

    async def _run_pipeline(self, execution_id: str) -> None:
        """تشغيل سير العمل execution."""
        async with self._lock:
            execution = self._executions.get(execution_id)
            pipeline = self._pipelines.get(execution_id and execution.pipeline_id)

        if not execution or not pipeline:
            return

        execution.status = PipelineStepStatus.RUNNING

        try:
            for step in pipeline.steps:
                # التحقق من الاعتمادات
                if step.depends_on:
                    for dep_id in step.depends_on:
                        dep_result = execution.steps_results.get(dep_id)
                        if dep_result is None or dep_result.get("status") != "completed":
                            step.status = PipelineStepStatus.SKIPPED
                            continue

                # تنفيذ الخطوة
                step.status = PipelineStepStatus.RUNNING
                step.started_at = datetime.now(UTC)

                try:
                    result = await self._execute_step(step, pipeline, execution)
                    step.status = PipelineStepStatus.COMPLETED
                    step.result = result
                    execution.steps_results[step.id] = {
                        "status": "completed",
                        "result": result,
                    }
                except Exception as exc:
                    step.status = PipelineStepStatus.FAILED
                    step.error = str(exc)
                    execution.steps_results[step.id] = {
                        "status": "failed",
                        "error": str(exc),
                    }
                    log.error(
                        "فشل في خطوة السير العمل %s: %s",
                        step.id,
                        exc,
                    )

                step.completed_at = datetime.now(UTC)

            # تحديد حالة التنفيذ النهائية
            failed_steps = [
                s for s in pipeline.steps if s.status == PipelineStepStatus.FAILED
            ]
            if failed_steps:
                execution.status = PipelineStepStatus.FAILED
                execution.error = f"{len(failed_steps)} خطوة فشلت"
            else:
                execution.status = PipelineStepStatus.COMPLETED

            execution.completed_at = datetime.now(UTC)

            # تحديث إحصائيات خط السير
            async with self._lock:
                pipeline = self._pipelines.get(execution.pipeline_id)
                if pipeline:
                    pipeline.last_run = execution.completed_at
                    pipeline.run_count += 1
                    if execution.status == PipelineStepStatus.COMPLETED:
                        pipeline.success_count += 1
                    else:
                        pipeline.failure_count += 1

            log.info(
            "انتهى تنفيذ سير العمل: %s (الحالة: %s)",
            execution.pipeline_id,
            execution.status.value,
        )

        except Exception as exc:
            execution.status = PipelineStepStatus.FAILED
            execution.error = str(exc)
            execution.completed_at = datetime.now(UTC)
            log.error("فشل في تنفيذ سير العمل %s: %s", execution.pipeline_id, exc)

    async def _execute_step(
        self,
        step: PipelineStep,
        pipeline: AutomationPipeline,
        execution: PipelineExecution,
    ) -> Any:
        """تنفيذ خطوة واحدة من سير العمل."""
        action_type = step.action_type
        params = step.parameters

        if action_type == "create_issue":
            return await self._create_issue_step(pipeline.owner, pipeline.repo, params)
        elif action_type == "create_pull_request":
            return await self._create_pr_step(pipeline.owner, pipeline.repo, params)
        elif action_type == "create_release":
            return await self._create_release_step(pipeline.owner, pipeline.repo, params)
        elif action_type == "send_notification":
            return await self._send_notification_step(params, execution.trigger_event)
        elif action_type == "update_issue":
            return await self._update_issue_step(pipeline.owner, pipeline.repo, params)
        elif action_type == "close_issue":
            return await self._close_issue_step(pipeline.owner, pipeline.repo, params)
        else:
            raise ValueError(f"نوع إجراء غير مدعوم: {action_type}")

    async def _create_issue_step(
        self, owner: str, repo: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """خطوة إنشاء قضية."""
        issue = await self.issue_manager.create_issue(
            owner=owner,
            repo=repo,
            title=params.get("title", "قضية تلقائية"),
            body=params.get("body", ""),
            assignees=params.get("assignees"),
            labels=params.get("labels"),
        )
        return {"issue_number": issue.number, "issue_url": issue.html_url}

    async def _create_pr_step(
        self, owner: str, repo: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """خطوة إنشاء طلب سحب."""
        pr = await self.pr_manager.create_pull_request(
            owner=owner,
            repo=repo,
            title=params.get("title", "طلب سحب تلقائي"),
            head=params.get("head", ""),
            base=params.get("base", "main"),
            body=params.get("body"),
        )
        return {"pr_number": pr.number, "pr_url": pr.html_url}

    async def _create_release_step(
        self, owner: str, repo: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """خطوة إنشاء إصدار."""
        release = await self.release_manager.create_release(
            owner=owner,
            repo=repo,
            tag_name=params.get("tag_name", "v1.0.0"),
            name=params.get("name"),
            body=params.get("body"),
        )
        return {"release_id": release.id, "tag_name": release.tag_name}

    async def _send_notification_step(
        self, params: dict[str, Any], trigger_event: dict[str, Any]
    ) -> dict[str, Any]:
        """خطوة إرسال إشعار."""
        # في النظام الحقيقي، يمكن إرسال إشعار عبر Telegram أو بريد إلكتروني
        message = params.get(
            "message",
            f"حدث حدث في المستودع: {trigger_event.get('action', 'غير محدد')}",
        )
        log.info("إشعار تلقائي: %s", message)
        return {"message": message, "sent": True}

    async def _update_issue_step(
        self, owner: str, repo: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """خطوة تحديث قضية."""
        issue_number = params.get("issue_number")
        if not issue_number:
            raise ValueError("issue_number مطلوب لتحديث القضية")

        issue = await self.issue_manager.edit_issue(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            title=params.get("title"),
            body=params.get("body"),
            state=params.get("state"),
        )
        return {"issue_number": issue.number, "state": issue.state}

    async def _close_issue_step(
        self, owner: str, repo: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """خطوة إغلاق قضية."""
        issue_number = params.get("issue_number")
        if not issue_number:
            raise ValueError("issue_number مطلوب لإغلاق القضية")

        issue = await self.issue_manager.close_issue(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
        )
        return {"issue_number": issue.number, "state": issue.state}

    async def get_execution(self, execution_id: str) -> PipelineExecution | None:
        """الحصول على تنفيذ بمعرفه."""
        async with self._lock:
            return self._executions.get(execution_id)

    async def list_executions(
        self,
        pipeline_id: str | None = None,
        limit: int = 100,
    ) -> list[PipelineExecution]:
        """قائمة عمليات التنفيذ."""
        async with self._lock:
            executions = list(self._executions.values())

            if pipeline_id:
                executions = [e for e in executions if e.pipeline_id == pipeline_id]

            executions.sort(key=lambda e: e.started_at, reverse=True)
            return executions[:limit]

    async def get_pipeline_statistics(self, pipeline_id: str) -> dict[str, Any]:
        """الحصول على إحصائيات سير العمل."""
        async with self._lock:
            pipeline = self._pipelines.get(pipeline_id)
            if pipeline is None:
                return {}

            executions = [
                e for e in self._executions.values()
                if e.pipeline_id == pipeline_id
            ]

            total_executions = len(executions)
            successful = sum(1 for e in executions if e.status == PipelineStepStatus.COMPLETED)
            failed = sum(1 for e in executions if e.status == PipelineStepStatus.FAILED)

            return {
                "pipeline_id": pipeline_id,
                "pipeline_name": pipeline.name,
                "total_executions": total_executions,
                "successful_executions": successful,
                "failed_executions": failed,
                "success_rate": round(successful / max(1, total_executions) * 100, 1),
                "last_run": pipeline.last_run.isoformat() if pipeline.last_run else None,
                "is_enabled": pipeline.is_enabled,
            }

