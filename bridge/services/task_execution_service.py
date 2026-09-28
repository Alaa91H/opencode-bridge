"""Framework-independent orchestration for one queued agent task."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from attachments import AttachmentError, attachment_prompt_note
from messages import empty_response_message
from opencode_client import extract_file_response, extract_text_response
from prompt_enhancer import ResearchMode, enhance_prompt


class TaskRepositoryPort(Protocol):
    async def get(self, task_id: int) -> Any | None: ...
    async def finish(self, task_id: int, success: bool, error: str | None = None) -> None: ...


class AttachmentStorePort(Protocol):
    def validate_input_records(self, records: Any) -> list[Any]: ...
    def task_output_directory(self, task_id: int) -> Any: ...
    def task_work_directory(self, task_id: int) -> Any: ...
    def collect_task_outputs(self, task_id: int, max_files: int = 10) -> list[Any]: ...
    def cleanup_task_work(self, task_id: int) -> None: ...
    def delete_input_records(self, records: Any) -> int: ...


class ReporterPort(Protocol):
    async def record(
        self,
        phase: str,
        message: str,
        kind: str = "info",
        force: bool = False,
    ) -> None: ...
    async def consume_events(self, client: Any, session_id: str) -> None: ...
    async def finalize_text(
        self,
        text: str,
        status: str = "completed",
        message: str = "اكتمل التنفيذ.",
    ) -> None: ...


class ExecutionDeliveryPort(Protocol):
    async def begin(self, task: Any) -> ReporterPort: ...
    async def send_outputs(self, task: Any, paths: list[Any]) -> int: ...
    def final_text(self, text: str) -> str: ...
    def error_text(self, exc: Exception, operation: str) -> str: ...
    async def end(self, task: Any) -> None: ...


AuditWrite = Callable[..., None]


class TaskExecutionService:
    """Execute queue tasks without importing Telegram or application globals."""

    def __init__(
        self,
        repository: TaskRepositoryPort,
        agent_service: Any,
        attachment_store: AttachmentStorePort,
        *,
        audit_write: AuditWrite,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repository = repository
        self.agent_service = agent_service
        self.attachment_store = attachment_store
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)

    async def execute(self, task: Any, delivery: ExecutionDeliveryPort) -> None:
        current = await self.repository.get(task.id)
        if current is None or current.status == "cancelled":
            return

        reporter = await delivery.begin(task)
        event_task: asyncio.Task[None] | None = None
        try:
            requested_mode: ResearchMode | None = None
            if task.execution_mode:
                try:
                    requested_mode = ResearchMode(task.execution_mode)
                except ValueError:
                    self.log.warning(
                        "تم تجاهل وضع تنفيذ غير معروف للمهمة %s",
                        task.id,
                    )

            enhanced = enhance_prompt(task.prompt, requested_mode=requested_mode)
            self.audit_write(
                "task_started",
                "accepted",
                actor_id=task.owner_id,
                details={
                    "task_id": task.id,
                    "scheduled": task.is_recurring,
                    "attachment_count": len(task.attachments),
                    "intent": enhanced.intent,
                    "research_depth": enhanced.research_depth,
                    "requested_mode": requested_mode,
                },
            )

            await reporter.record(
                "preparing",
                "عم نتحقق من المرفقات ونحضّر مساحة النتيجة.",
            )
            attachments = self.attachment_store.validate_input_records(task.attachments)
            output_directory = self.attachment_store.task_output_directory(task.id)
            work_directory = self.attachment_store.task_work_directory(task.id)
            prompt = enhanced.text + "\n\n" + attachment_prompt_note(
                attachments,
                output_directory,
                work_directory,
            )
            message_parts = [
                attachment.to_message_part()
                for attachment in attachments
                if attachment.is_direct_model_visible()
            ]

            session_id, selected_model = await self.agent_service.current_model(task.owner_id)
            needs_image_input = any(
                attachment.mime.startswith("image/")
                or attachment.mime == "application/pdf"
                or attachment.kind in {"video", "animation", "video_note"}
                for attachment in attachments
            )
            if needs_image_input:
                media_model = await self.agent_service.best_model_for_inputs(
                    selected_model,
                    {"image"},
                )
                if media_model and media_model != selected_model:
                    self.audit_write(
                        "task_media_model_selected",
                        "changed",
                        actor_id=task.owner_id,
                        details={
                            "task_id": task.id,
                            "from_model": selected_model,
                            "to_model": media_model,
                            "required_input": "image",
                        },
                    )
                    selected_model = media_model

            await reporter.record(
                "session",
                "تم تجهيز جلسة الوكيل. عم نبدأ التنفيذ.",
            )
            event_task = asyncio.create_task(
                reporter.consume_events(self.agent_service.client, session_id)
            )
            started_at = time.monotonic()
            await reporter.record(
                "processing",
                "الوكيل استلم المهمة وعم يعالجها.",
            )

            response, selected_model = (
                await self.agent_service.send_prompt_with_fallback(
                    task.owner_id,
                    session_id,
                    prompt,
                    message_parts,
                    selected_model,
                    audit_write=self.audit_write,
                    task_id=task.id,
                )
            )
            reply_text = extract_text_response(response)
            output_files = self.attachment_store.collect_task_outputs(task.id)

            if not reply_text and not output_files:
                await reporter.record(
                    "retry",
                    "ما وصل ناتج واضح؛ عم نجرب مرة أخيرة.",
                    "warning",
                    force=True,
                )
                response, selected_model = (
                    await self.agent_service.send_prompt_with_fallback(
                        task.owner_id,
                        session_id,
                        prompt,
                        message_parts,
                        selected_model,
                        audit_write=self.audit_write,
                        task_id=task.id,
                    )
                )
                reply_text = extract_text_response(response)
                output_files = self.attachment_store.collect_task_outputs(task.id)

            elapsed = time.monotonic() - started_at
            current = await self.repository.get(task.id)
            if current is None or current.status == "cancelled":
                await reporter.finalize_text(
                    "تم إلغاء الطلب.",
                    status="cancelled",
                    message="تم إلغاء الطلب قبل تسليم النتيجة.",
                )
                self.audit_write(
                    "task_cancelled",
                    "cancelled",
                    actor_id=task.owner_id,
                    details={"task_id": task.id},
                )
                return

            if not reply_text and not output_files:
                await self.repository.finish(
                    task.id,
                    success=False,
                    error="empty_response",
                )
                await reporter.finalize_text(
                    delivery.final_text(empty_response_message()),
                    status="failed",
                    message="لم يرجع الوكيل نتيجة واضحة بعد إعادة المحاولة.",
                )
                self.audit_write(
                    "task_finished",
                    "empty_response",
                    actor_id=task.owner_id,
                    details={
                        "task_id": task.id,
                        "duration_seconds": round(elapsed, 2),
                        "agent_file_parts": len(extract_file_response(response)),
                    },
                )
                return

            await reporter.record(
                "delivering",
                "جاري تجهيز النتيجة النهائية…",
                force=True,
            )
            delivered_files = await delivery.send_outputs(task, output_files)
            final_body = reply_text or (
                "تم تجهيز الملفات المطلوبة."
                if delivered_files
                else empty_response_message()
            )
            await self.repository.finish(task.id, success=True)
            await reporter.finalize_text(
                delivery.final_text(final_body),
                status="completed",
                message="اكتمل التنفيذ.",
            )
            self.audit_write(
                "task_finished",
                "success",
                actor_id=task.owner_id,
                details={
                    "task_id": task.id,
                    "duration_seconds": round(elapsed, 2),
                    "response_length": len(reply_text),
                    "attachment_count": len(attachments),
                    "output_files": delivered_files,
                    "agent_file_parts": len(extract_file_response(response)),
                },
            )
        except AttachmentError as exc:
            await self.repository.finish(
                task.id,
                success=False,
                error="attachment_error",
            )
            await reporter.finalize_text(
                f"تعذر التعامل مع أحد الملفات: {exc}.",
                status="failed",
                message="تعذر تجهيز أحد الملفات.",
            )
            self.audit_write(
                "task_finished",
                "attachment_error",
                actor_id=task.owner_id,
                details={"task_id": task.id},
            )
        except Exception as exc:
            current = await self.repository.get(task.id)
            if current is None or current.status != "cancelled":
                await self.repository.finish(
                    task.id,
                    success=False,
                    error=type(exc).__name__,
                )
                await reporter.finalize_text(
                    delivery.error_text(exc, "تنفيذ الطلب"),
                    status="failed",
                    message="تعذر إكمال الطلب.",
                )
                self.audit_write(
                    "task_finished",
                    "error",
                    actor_id=task.owner_id,
                    details={
                        "task_id": task.id,
                        "error_type": type(exc).__name__,
                    },
                )
            else:
                await reporter.finalize_text(
                    "تم إلغاء الطلب.",
                    status="cancelled",
                    message="تم إلغاء الطلب أثناء التنفيذ.",
                )
        finally:
            try:
                self.attachment_store.cleanup_task_work(task.id)
            except Exception as exc:
                self.log.warning(
                    "تعذر تنظيف مساحة العمل المؤقتة للمهمة %s: %s",
                    task.id,
                    type(exc).__name__,
                )
            if task.attachments and not task.is_recurring:
                try:
                    self.attachment_store.delete_input_records(task.attachments)
                except Exception as exc:
                    self.log.warning(
                        "تعذر تنظيف مرفقات الإدخال للمهمة %s: %s",
                        task.id,
                        type(exc).__name__,
                    )
            if event_task is not None:
                event_task.cancel()
                try:
                    await event_task
                except asyncio.CancelledError:
                    pass
            await delivery.end(task)
