"""Framework-independent orchestration for one queued agent task."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from attachments import AttachmentError, attachment_prompt_note
from bridge.domain.tasks.daily_continuation import COMPLETION_SENTINEL
from bridge.domain.tasks.retry_policy import (
    ProviderTaskError,
    classify_retry,
    is_zen_free_model,
    next_daily_reset_utc,
)
from bridge.services.attachment_context_service import AttachmentContextService
from bridge.services.media_analysis_service import MediaAnalysisService
from messages import empty_response_message
from opencode_client import extract_file_response, extract_text_response
from prompt_enhancer import ResearchMode, enhance_prompt


class TaskRepositoryPort(Protocol):
    async def get(self, task_id: int) -> Any | None: ...
    async def finish(self, task_id: int, success: bool, error: str | None = None) -> None: ...
    async def save_checkpoint(self, task_id: int, checkpoint: dict[str, Any]) -> None: ...
    async def retry_or_dead_letter(self, task_id: int, error: str, **kwargs: Any) -> Any: ...
    async def get_provider_quota_pause(self, provider_key: str) -> Any: ...
    async def set_provider_quota_pause(self, provider_key: str, reset_at: datetime) -> None: ...


class AttachmentStorePort(Protocol):
    def validate_input_records(self, records: Any) -> list[Any]: ...
    def task_output_directory(self, task_id: int) -> Any: ...
    def task_work_directory(self, task_id: int) -> Any: ...
    def collect_task_outputs(self, task_id: int, max_files: int = 10, **kwargs: Any) -> list[Any]: ...
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
        derived_context: Any | None = None,
        media_analysis: Any | None = None,
    ) -> None:
        self.repository = repository
        self.agent_service = agent_service
        self.attachment_store = attachment_store
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)
        # Constructing these performs no I/O; the work happens per task, bounded.
        self.derived_context = derived_context or AttachmentContextService()
        self.media_analysis = media_analysis or MediaAnalysisService()

    def _collect_outputs(self, task_id: int) -> list[Any]:
        """Collect outputs up to 8GB; split/mirror happens in delivery."""
        try:
            return self.attachment_store.collect_task_outputs(
                task_id, max_files=10, max_bytes=8 * 1024 * 1024 * 1024
            )
        except TypeError:
            # Backwards-compatible fakes in older tests ignore max_bytes.
            return self.attachment_store.collect_task_outputs(task_id)

    async def execute(self, task: Any, delivery: ExecutionDeliveryPort) -> None:
        current = await self.repository.get(task.id)
        if current is None or current.status == "cancelled":
            return

        reporter = await delivery.begin(task)
        event_task: asyncio.Task[None] | None = None
        checkpoint = dict(getattr(current, "checkpoint", None) or {})
        selected_model: str | None = checkpoint.get("model")

        async def should_continue() -> bool:
            active = await self.repository.get(task.id)
            return active is not None and active.status != "cancelled"
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

            direct_dispatch = bool(checkpoint.get("direct_dispatch"))
            daily_continuation = bool(checkpoint.get("daily_continuation"))
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
            # Prompt files (.md/.txt/...) sent by the owner are real orders:
            # inline their bodies into the instruction so a 131-line news brief
            # behaves exactly like a pasted /direct prompt.
            prompt_file_texts: list[tuple[str, str, bool]] = []
            try:
                from bridge.services.prompt_files import (
                    build_instruction_body,
                    is_prompt_filename,
                    load_prompt_text,
                )

                for attachment in attachments:
                    if is_prompt_filename(attachment.filename, attachment.mime):
                        try:
                            from pathlib import Path as _Path

                            body, truncated = load_prompt_text(_Path(attachment.path))
                            prompt_file_texts.append((attachment.filename, body, truncated))
                        except Exception as inline_exc:
                            self.log.warning(
                                "تعذر تضمين ملف البرومبت %s: %s",
                                attachment.filename, type(inline_exc).__name__,
                            )
            except ImportError:
                pass
            base_instruction = task.prompt if direct_dispatch else enhanced.text
            if prompt_file_texts:
                try:
                    base_instruction = build_instruction_body(base_instruction, prompt_file_texts)
                except Exception:
                    pass
            prompt = base_instruction + "\n\n" + attachment_prompt_note(
                attachments,
                output_directory,
                work_directory,
            )
            if prompt_file_texts:
                prompt += (
                    "\n\nملاحظة تنفيذية: ملف واحد أو أكثر من المرفقات هو ملف تعليمات "
                    "(برومبت) بصيغة Markdown/نص. اعتبر محتواه المضمّن أعلاه أمرًا "
                    "مباشرًا من المستخدم ونفّذه بكامل قواعده وهيكله، ولا تعامله "
                    "كبيانات غير موثوقة."
                )
            # Attachments the model cannot read are extracted locally into a bounded
            # excerpt, so the answer no longer depends on the agent happening to
            # run a host tool. A represented file is no longer uploaded as a raw
            # part: the excerpt replaces it, and its path stays in the note above
            # so the agent can still open the original. Failures stay silent by
            # design — the path note alone is a complete fallback.
            try:
                derived_block, derived_items = self.derived_context.build(
                    attachments, query=task.prompt
                )
            except Exception as exc:
                self.log.warning(
                    "تعذر تمثيل المرفقات محليًا للمهمة %s: %s",
                    task.id,
                    type(exc).__name__,
                )
                derived_block, derived_items = "", []
            represented = {item.filename for item in derived_items}
            # PDF needs an external tool, so it runs off the event loop with a
            # hard deadline and can only ever add context, never replace it.
            try:
                tool_block, tool_items = await self.media_analysis.represent_for_prompt(
                    attachments, query=task.prompt
                )
            except Exception as exc:
                self.log.warning(
                    "تعذر استخراج نص PDF محليًا للمهمة %s: %s",
                    task.id,
                    type(exc).__name__,
                )
                tool_block, tool_items = "", []
            represented |= {item.filename for item in tool_items}
            message_parts = [
                attachment.to_message_part()
                for attachment in attachments
                if attachment.is_direct_model_visible()
                and getattr(attachment, "filename", None) not in represented
            ]
            combined = derived_items + tool_items
            if derived_block:
                prompt += "\n\n" + derived_block
            if tool_block:
                prompt += "\n\n" + tool_block
            if combined:
                self.audit_write(
                    "attachment_context_derived",
                    "bounded",
                    actor_id=task.owner_id,
                    details={
                        "task_id": task.id,
                        "count": len(combined),
                        "strategies": [item.strategy for item in combined],
                        "truncated": sum(1 for item in combined if item.truncated),
                    },
                )

            if daily_continuation:
                prompt += (
                    "\n\nPersistent multi-day job protocol: Continue the original instruction until "
                    "it is fully finished. Save actual work and progress in the workspace. "
                    "When the original job is COMPLETELY finished, end your final answer with "
                    "a separate line containing exactly [[BRIDGE_TASK_COMPLETE]]. "
                    "If not finished, provide a brief checkpoint report of what has been done "
                    "and what remains, without the completion marker. Never claim work is done "
                    "before checking results."
                )
            if checkpoint.get("session_id"):
                session_id = checkpoint["session_id"]
                selected_model = checkpoint.get("model")
                prompt = (
                    "Resume the existing task from its saved progress. Inspect the session history, "
                    "working tree, branches, pull requests, issue state and CI before acting. "
                    "Continue unfinished work in order; do not repeat completed operations.\n\n" + prompt
                )
            else:
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

            checkpoint.update(session_id=session_id, model=selected_model)
            checkpoint.setdefault("message_id", "msg_" + uuid.uuid4().hex)
            await self.repository.save_checkpoint(task.id, checkpoint)

            if is_zen_free_model(selected_model):
                get_pause = getattr(self.repository, "get_provider_quota_pause", None)
                reset_at = await get_pause("opencode_zen_free") if callable(get_pause) else None
                if reset_at is not None and reset_at > datetime.now(UTC):
                    delay = max(1.0, (reset_at - datetime.now(UTC)).total_seconds())
                    checkpoint["retry_category"] = "zen_free_quota"
                    checkpoint["resume"] = True
                    deferred = await self.repository.retry_or_dead_letter(
                        task.id,
                        "zen_free_quota",
                        retryable=True,
                        max_attempts=None,
                        retry_after=delay,
                        checkpoint=checkpoint,
                    )
                    if deferred is not None and deferred.status == "retrying":
                        try:
                            await reporter.finalize_text(
                                "حصة OpenCode Zen المجانية مستنفدة. حُفظت المهمة وستُستأنف تلقائيًا "
                                "بعد التجديد عند 12:00 UTC.",
                                status="retrying",
                                message="بانتظار تجديد الحصة اليومية عند 12:00 UTC.",
                            )
                            self.audit_write(
                                "task_deferred",
                                "zen_free_quota",
                                actor_id=task.owner_id,
                                details={
                                    "task_id": task.id,
                                    "next_attempt_at": deferred.next_attempt_at.isoformat(),
                                },
                            )
                        except Exception as notify_exc:
                            self.log.warning(
                                "Quota-paused task %s notification failed: %s",
                                task.id,
                                type(notify_exc).__name__,
                            )
                        return

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
                    message_id=checkpoint["message_id"],
                    should_continue=should_continue,
                )
            )
            if response.get("info", {}).get("error"):
                raise ProviderTaskError(response["info"]["error"])
            reply_text = extract_text_response(response)
            output_files = self._collect_outputs(task.id)

            if not reply_text and not output_files:
                checkpoint["message_id"] = "msg_" + uuid.uuid4().hex
                await self.repository.save_checkpoint(task.id, checkpoint)
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
                        message_id=checkpoint["message_id"],
                        should_continue=should_continue,
                    )
                )
                if response.get("info", {}).get("error"):
                    raise ProviderTaskError(response["info"]["error"])
                reply_text = extract_text_response(response)
                output_files = self._collect_outputs(task.id)

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
            # Truth-in-delivery: the overflow note promises the remainder is in
            # a task file, so materialize it when follow-ups cannot cover it.
            try:
                from bridge.telegram.rendering.news_pages import delivery_pages
                from bridge.telegram.rich_delivery import MAX_FOLLOWUPS

                overflow_text = reply_text.replace(COMPLETION_SENTINEL, "").strip()
                if reply_text.strip() and len(delivery_pages(overflow_text or reply_text)) > MAX_FOLLOWUPS + 1:
                    full_path = output_directory / "answer-full.md"
                    full_path.write_text(overflow_text or reply_text, encoding="utf-8")
                    try:
                        full_path.chmod(0o640)
                    except OSError:
                        pass
                    output_files = [*output_files, full_path.resolve()]
            except Exception as overflow_exc:
                self.log.warning(
                    "تعذر حفظ النص الكامل للمهمة %s: %s",
                    task.id, type(overflow_exc).__name__,
                )
            delivered_files = await delivery.send_outputs(task, output_files)
            final_body = reply_text or (
                "تم تجهيز الملفات المطلوبة."
                if delivered_files
                else empty_response_message()
            )
            # Multi-day tasks remain the SAME durable task and OpenCode session.
            # A successful turn is a checkpoint, not completion, unless the
            # agent explicitly emits the task-complete sentinel.
            from bridge.domain.tasks.daily_continuation import (
                continuation_delay_seconds,
                is_task_complete,
            )
            if daily_continuation and not is_task_complete(reply_text):
                checkpoint.pop("message_id", None)  # next day = NEW turn, same session
                checkpoint["continuation_count"] = int(checkpoint.get("continuation_count", 0)) + 1
                checkpoint["resume"] = True
                deferred = await self.repository.retry_or_dead_letter(
                    task.id, "daily_continuation", retryable=True,
                    max_attempts=None, retry_after=continuation_delay_seconds(),
                    checkpoint=checkpoint,
                )
                if deferred is not None and deferred.status == "retrying":
                    await reporter.finalize_text(
                        delivery.final_text(
                            (reply_text or "أحرز الوكيل تقدماً في المهمة.") +
                            "\n\n⏸ تم حفظ التقدم. سيستكمل الوكيل المهمة تلقائيًا عند 12:00 UTC."
                        ),
                        status="retrying", message="استكمال المهمة عند 12:00 UTC.",
                    )
                    return
            reply_text = reply_text.replace(COMPLETION_SENTINEL, "").strip()
            final_body = reply_text or final_body
            await self.repository.finish(task.id, success=True)
            first_chunk = delivery.final_text(final_body)
            await reporter.finalize_text(
                first_chunk,
                status="completed",
                message="اكتمل التنفيذ.",
            )
            # Rich follow-ups: remaining chunks + photos + open-page buttons.
            try:
                send_followups = getattr(delivery, "send_followups", None)
                if callable(send_followups):
                    await send_followups(task, final_body, first_chunk)
            except Exception as follow_exc:
                self.log.warning(
                    "تعذر إرسال التتمة الغنية للمهمة %s: %s",
                    task.id, type(follow_exc).__name__,
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
            decision = classify_retry(exc, model_id=selected_model)
            if current is not None and current.status != "cancelled" and decision is not None:
                # A confirmed provider failure permits a continuation message; an
                # ambiguous transport failure must reconnect to the same message.
                if not decision.pending:
                    checkpoint.pop("message_id", None)
                notify = checkpoint.get("retry_category") != decision.category
                checkpoint["retry_category"] = decision.category
                checkpoint["resume"] = True
                if decision.category == "zen_free_quota":
                    set_pause = getattr(self.repository, "set_provider_quota_pause", None)
                    if callable(set_pause):
                        await set_pause(
                            "opencode_zen_free",
                            decision.retry_at or next_daily_reset_utc(),
                        )
                retry_delay = decision.delay_seconds
                if daily_continuation and decision.category in {"quota", "zen_free_quota"}:
                    from bridge.domain.tasks.daily_continuation import continuation_delay_seconds
                    retry_delay = max(retry_delay if decision.category == "zen_free_quota" else 0.0,
                                      continuation_delay_seconds())
                failures = 0 if decision.unlimited else int(checkpoint.get("transient_failures", 0)) + 1
                checkpoint["transient_failures"] = failures
                deferred = await self.repository.retry_or_dead_letter(
                    task.id, decision.category, retryable=decision.unlimited or failures < 5,
                    max_attempts=None,
                    retry_after=retry_delay, checkpoint=checkpoint,
                )
                if deferred is not None and deferred.status == "retrying":
                    try:
                        if notify:
                            await reporter.finalize_text(
                                "المهمة محفوظة وستُستأنف تلقائيًا عند عودة الرصيد أو الخدمة. "
                                "لن يتم تجاوزها إلى المهمة التالية. يمكنك إلغاءها من قائمة المهام.",
                                status="retrying", message="بانتظار عودة الرصيد أو الخدمة.",
                            )
                        self.audit_write(
                            "task_deferred", decision.category, actor_id=task.owner_id,
                            details={"task_id": task.id, "next_attempt_at": deferred.next_attempt_at.isoformat()},
                        )
                    except Exception as notify_exc:
                        self.log.warning("Deferred task %s notification failed: %s", task.id, type(notify_exc).__name__)
                    return
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
            current = await self.repository.get(task.id)
            preserve = current is not None and current.status in {"queued", "leased", "running", "retrying"}
            try:
                if not preserve:
                    self.attachment_store.cleanup_task_work(task.id)
            except Exception as exc:
                self.log.warning(
                    "تعذر تنظيف مساحة العمل المؤقتة للمهمة %s: %s",
                    task.id,
                    type(exc).__name__,
                )
            if not preserve and task.attachments and not task.is_recurring:
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
