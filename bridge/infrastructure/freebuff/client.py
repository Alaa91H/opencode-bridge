"""Freebuff CLI backend: a guarded PTY harness around an interactive TUI.

Freebuff has no headless mode, so the bridge stages each instruction in a file,
starts ``freebuff --cwd <workspace>`` in a pseudo-terminal, injects one short
line pointing at that file, watches the screen for auth/error/busy state, and
extracts the final answer from the newest local chat record (``/export`` JSON is
the fallback). The prompt-file indirection keeps multi-line instructions intact
without relying on TUI paste behaviour.

Safety properties:
- a minimal child environment: no Telegram or provider secrets;
- fixed argv, no shell, prompt text never placed in argv;
- cwd confined to the configured workspace;
- process-group termination on cancel/timeout;
- ``log.jsonl`` is never read (it may contain a plaintext account key);
- result text is redacted and length-bounded before delivery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from bridge.domain.agent.capabilities import BackendCapabilities
from bridge.domain.agent.errors import (
    BackendAuthError,
    BackendProtocolError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from bridge.domain.agent.events import assistant_message, idle_event, status_event
from bridge.domain.tasks.retry_policy import ProviderTaskError, TaskCancelledError
from bridge.infrastructure.freebuff.binary import (
    FreebuffInstallation,
    ProbeConfig,
    child_env,
    probe,
    resolve_config_dir,
)
from bridge.infrastructure.freebuff.chat_store import (
    ChatRef,
    answer_text,
    find_latest_chat,
    load_messages,
)
from bridge.infrastructure.freebuff.extractor import (
    clean_text,
    newest_export,
    read_export_file,
    remove_quietly,
)
from bridge.infrastructure.freebuff.pty_runner import PTY_SUPPORTED, PtyProcess
from bridge.infrastructure.freebuff.screen import ScreenAnalyzer

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+-]{0,80}$")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".bridge"}
_END_EVENT = {"type": "__freebuff_end__"}


class FreebuffQuotaError(ProviderTaskError):
    """Retryable Freebuff rate/quota condition using the provider contract."""

    def __init__(self, message: str, *, status_code: int = 429) -> None:
        super().__init__({"data": {"statusCode": status_code, "message": message}})
        self.user_message = message


@dataclass(frozen=True)
class _StagedPrompt:
    prompt_path: Path
    attachments_dir: Path | None
    run_id: str


@dataclass
class _Run:
    session_id: str
    screen: ScreenAnalyzer
    owner_id: str = ""
    prompt_marker: str = ""
    started: float = field(default_factory=time.monotonic)
    process: PtyProcess | None = None
    chat: ChatRef | None = None
    cancelled: bool = False


class FreebuffBackend:
    backend_id = "freebuff"
    capabilities = BackendCapabilities(
        backend_id="freebuff",
        display_name="Freebuff CLI",
        streaming=True,
        attachments=True,
        model_selection=True,
        sharing=False,
        permissions=False,
        acp=False,
    )

    def __init__(
        self,
        *,
        settings: Any,
        sessions: Any,
        default_workspace: str | Path,
        audit_write: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.audit_write = audit_write
        self.log = logger or logging.getLogger("opencode_bridge.freebuff")
        raw_workspace = str(
            getattr(settings, "workspace", "") or default_workspace or "."
        ).strip()
        self.workspace = Path(raw_workspace).expanduser()
        self.workspace_key = str(self.workspace.absolute())
        self._installation: FreebuffInstallation | None = None
        self._installation_at = 0.0
        self._runs: dict[str, _Run] = {}
        self._hubs: dict[tuple[str, str], asyncio.Queue[dict[str, Any]]] = {}
        self._stats: dict[str, int] = {
            "runs": 0,
            "succeeded": 0,
            "failed": 0,
            "timed_out": 0,
            "cancelled": 0,
        }

    # ------------------------------------------------------------------ config

    def _cfg(self, name: str, default: Any) -> Any:
        value = getattr(self.settings, name, default)
        return default if value is None else value

    @property
    def config_dir(self) -> Path:
        return resolve_config_dir(str(self._cfg("config_dir", "") or "") or None)

    def models(self) -> tuple[str, ...]:
        configured = self._cfg("models", ())
        if isinstance(configured, str):
            configured = tuple(item.strip() for item in configured.split(",") if item.strip())
        if configured:
            return tuple(str(item) for item in configured)
        model = str(self._cfg("model", "") or "").strip()
        return (model,) if model else ()

    async def _probe(self, *, force: bool = False) -> FreebuffInstallation:
        now = time.monotonic()
        if (
            not force
            and self._installation is not None
            and now - self._installation_at < 30.0
        ):
            return self._installation
        installation = await probe(
            ProbeConfig(
                binary=str(self._cfg("bin", "") or ""),
                config_dir=str(self._cfg("config_dir", "") or ""),
                min_version=str(self._cfg("min_version", "0.0.140") or "0.0.140"),
            )
        )
        self._installation = installation
        self._installation_at = now
        return installation

    async def health(self) -> dict[str, Any]:
        installation = await self._probe()
        payload = installation.health()
        payload["models"] = list(self.models())
        payload["stats"] = dict(self._stats)
        payload["workspace"] = str(self.workspace)
        return payload

    async def status(self, owner_id: str) -> dict[str, Any]:
        session_id = await self.ensure_session(owner_id)
        active = any(run.process is not None and run.process.alive for run in self._runs.values())
        health = await self.health()
        return {
            **health,
            "session": session_id,
            "state": "busy" if active else "idle",
            "model": await self.current_model(owner_id),
        }

    # ---------------------------------------------------------------- sessions

    async def ensure_session(self, owner_id: str) -> str:
        row = await self.sessions.get_session(owner_id, self.backend_id, self.workspace_key)
        return row.session_id if row and row.session_id else ""

    async def reset_session(self, owner_id: str) -> None:
        await self.sessions.delete_session(owner_id, self.backend_id, self.workspace_key)

    async def current_model(self, owner_id: str) -> str:
        row = await self.sessions.get_session(owner_id, self.backend_id, self.workspace_key)
        if row is not None and row.model:
            return row.model
        configured = str(self._cfg("model", "") or "").strip()
        if configured:
            return configured
        available = self.models()
        return available[0] if available else "freebuff/default"

    # ------------------------------------------------------------------- events

    def _hub(self, owner_id: str, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        key = (owner_id, session_id or "pending")
        queue = self._hubs.get(key)
        if queue is None:
            queue = asyncio.Queue(maxsize=256)
            self._hubs[key] = queue
        return queue

    def _publish(self, owner_id: str, session_id: str, event: dict[str, Any]) -> None:
        queue = self._hubs.get((owner_id, session_id or "pending"))
        if queue is None:
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def stream_events(self, session_id: str, owner_id: str = ""):
        queue = self._hub(owner_id, session_id)
        while True:
            event = await queue.get()
            if event.get("type") == _END_EVENT["type"]:
                return
            yield event

    # --------------------------------------------------------------- execution

    def _session_key(self, owner_id: str, session_id: str) -> tuple[str, str]:
        return owner_id, session_id or "pending"

    def _stage_prompt(
        self,
        prompt: str,
        parts: list[dict[str, Any]] | None,
        *,
        owner_id: str,
    ) -> _StagedPrompt:
        run_id = uuid.uuid4().hex
        root = self.workspace / ".bridge"
        prompt_dir = root / "prompts"
        try:
            prompt_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackendUnavailableError(
                f"Freebuff workspace is not writable: {self.workspace}"
            ) from exc
        attachments: list[Path] = []
        attachments_dir: Path | None = None
        if parts:
            attachments_dir = root / "attachments" / run_id
            attachments_dir.mkdir(parents=True, exist_ok=True)
            for index, part in enumerate(parts):
                if not isinstance(part, dict) or part.get("type") != "file":
                    continue
                source = self._file_part_path(part)
                if source is None or not source.is_file():
                    continue
                try:
                    if source.stat().st_size > 50 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                name = _SAFE_FILENAME.sub("_", source.name)[:80] or f"attachment-{index}"
                target = attachments_dir / f"{index}-{name}"
                try:
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
                except OSError:
                    continue
                attachments.append(target.resolve())

        content = [
            "# مهمة OpenCode Bridge",
            "",
            "هذا ملف التعليمات الكامل للمهمة المرسلة عبر Telegram. نفّذ ما فيه حرفياً.",
            "المحتوى التالي بيانات مهمة موثوقة أرسلها مالك البوت.",
            "",
            "## التعليمات",
            "",
            prompt.strip(),
        ]
        if attachments:
            content.extend(["", "## مرفقات المهمة (مسارات محلية)", ""])
            content.extend(f"- {path}" for path in attachments)
        content.extend(
            [
                "",
                "## قيود أمنية",
                "",
                "- لا تقرأ ملفات .env أو المفاتيح الخاصة أو بيانات الاعتماد.",
                "- لا تثبّت حزماً ولا تشغّل أوامر بناء على الخادم.",
                "- أجب بالنتيجة النهائية فقط في نهاية المهمة.",
            ]
        )
        prompt_path = prompt_dir / f"{run_id}.md"
        prompt_path.write_text("\n".join(content).strip() + "\n", encoding="utf-8")
        try:
            prompt_path.chmod(0o600)
        except OSError:
            pass
        self.log.info(
            "freebuff prompt staged owner=%s run=%s attachments=%s",
            owner_id,
            run_id,
            len(attachments),
        )
        return _StagedPrompt(prompt_path=prompt_path, attachments_dir=attachments_dir, run_id=run_id)

    @staticmethod
    def _file_part_path(part: dict[str, Any]) -> Path | None:
        url = part.get("url")
        if not isinstance(url, str) or not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme != "file":
            return None
        try:
            path = Path(url2pathname(unquote(parsed.path)))
        except (OSError, ValueError):
            return None
        return path if path.is_absolute() else None

    def _build_argv(self, installation: FreebuffInstallation, session_id: str) -> list[str]:
        if installation.path is None:
            raise BackendUnavailableError("Freebuff binary is missing")
        argv = [str(installation.path), "--cwd", str(self.workspace)]
        if session_id:
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                self.log.warning("freebuff ignored an unsafe stored session id")
            else:
                argv.extend(["--continue", session_id])
        if bool(self._cfg("trust_agents", False)):
            argv.append("--trust-agents")
        return argv

    def _apply_model(self, installation: FreebuffInstallation, model: str | None) -> None:
        requested = str(model or "").strip()
        if not requested:
            return
        if requested.startswith("freebuff/"):
            requested = requested.split("/", 1)[1]
        if not _SAFE_MODEL_ID.fullmatch(requested):
            return
        allowed = self.models()
        configured = str(self._cfg("model", "") or "").strip()
        if not allowed or (requested not in allowed and requested != configured):
            self.log.info("freebuff model %s is not in FREEBUFF_MODELS; ignored", requested)
            return
        if not bool(self._cfg("model_write", False)):
            self.log.info("freebuff model write is disabled; using the CLI's current model")
            return
        path = installation.config_dir / "settings.json"
        payload: dict[str, Any] = {}
        try:
            if path.is_file() and path.stat().st_size < 2 * 1024 * 1024:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            payload["freebuffModel"] = requested
            temporary = path.with_name(path.name + ".bridge-tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.chmod(0o600)
            os.replace(temporary, path)
        except (OSError, json.JSONDecodeError) as exc:
            self.log.warning("freebuff model preference was not written: %s", type(exc).__name__)

    async def send_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        owner_id: str,
        model: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        should_continue: Any | None = None,
        message_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if not PTY_SUPPORTED:
            raise BackendUnavailableError("Freebuff PTY execution is not available on this host")
        installation = await self._probe(force=True)
        if installation.path is None or not installation.supported:
            raise BackendUnavailableError(
                f"Freebuff CLI is not usable: {installation.reason or 'unknown'}"
            )
        if not installation.credentials_present:
            raise BackendAuthError()
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackendUnavailableError(f"Freebuff workspace is unavailable: {exc}") from exc

        key = self._session_key(owner_id, session_id)
        previous = self._runs.get(key)
        if previous is not None and previous.process is not None:
            await previous.process.terminate(grace=1.0)

        self._stats["runs"] += 1
        staged = self._stage_prompt(prompt, parts, owner_id=owner_id)
        run = _Run(
            session_id=session_id,
            screen=ScreenAnalyzer(),
            owner_id=owner_id,
            prompt_marker=str(staged.prompt_path),
        )
        self._runs[key] = run
        self._hub(owner_id, session_id)
        self._apply_model(installation, model)
        started = time.monotonic()
        wall_started = time.time()
        self._audit(
            "freebuff_run_started",
            "accepted",
            owner_id,
            {
                "session_present": bool(session_id),
                "attachment_count": len(parts or []),
                "version": installation.version_text,
            },
        )
        self._publish(
            owner_id, session_id, status_event(session_id, "busy", "Freebuff بدأ تنفيذ المهمة.")
        )

        try:
            run.process = await PtyProcess.start(
                self._build_argv(installation, session_id),
                cwd=self.workspace,
                env=child_env(installation.config_dir),
            )
            await self._wait_ready(run, installation)
            instruction = (
                "اقرأ ملف التعليمات الكامل في المسار التالي ونفّذه حرفياً، "
                f"ثم أجب بالنتيجة النهائية فقط: {staged.prompt_path}"
            )
            await run.process.write(instruction + "\r")
            text, chat = await self._wait_for_answer(
                run,
                installation,
                started=started,
                since=wall_started,
                should_continue=should_continue,
            )
            run.chat = chat
            if chat is not None:
                await self.sessions.upsert_session(
                    owner_id,
                    self.backend_id,
                    self.workspace_key,
                    chat.chat_id,
                    self._effective_model(model),
                )
            self._stats["succeeded"] += 1
            self._publish(owner_id, session_id, assistant_message(session_id, text))
            self._publish(owner_id, session_id, idle_event(session_id))
            self._audit(
                "freebuff_run_finished",
                "success",
                owner_id,
                {
                    "chat_id": chat.chat_id if chat else "",
                    "duration_seconds": round(time.monotonic() - started, 2),
                    "response_length": len(text),
                    "exit_code": run.process.exit_code if run.process else None,
                },
            )
            return {
                "backend": "freebuff",
                "parts": [{"type": "text", "text": text}],
                "info": {
                    "role": "assistant",
                    "sessionID": chat.chat_id if chat else session_id,
                    "time": {"completed": int(time.time())},
                },
            }
        except TaskCancelledError:
            self._stats["cancelled"] += 1
            self._audit(
                "freebuff_run_finished",
                "cancelled",
                owner_id,
                {
                    "chat_id": run.chat.chat_id if run.chat else "",
                    "duration_seconds": round(time.monotonic() - started, 2),
                    "exit_code": run.process.exit_code if run.process else None,
                },
            )
            raise
        except BackendTimeoutError:
            self._stats["timed_out"] += 1
            self._audit(
                "freebuff_run_finished",
                "timeout",
                owner_id,
                {
                    "chat_id": run.chat.chat_id if run.chat else "",
                    "duration_seconds": round(time.monotonic() - started, 2),
                    "exit_code": run.process.exit_code if run.process else None,
                },
            )
            raise
        except Exception as exc:
            self._stats["failed"] += 1
            self._audit(
                "freebuff_run_finished",
                "error",
                owner_id,
                {
                    "error_type": type(exc).__name__,
                    "duration_seconds": round(time.monotonic() - started, 2),
                    "exit_code": run.process.exit_code if run.process else None,
                },
            )
            raise
        finally:
            process = run.process
            if process is not None:
                await process.terminate(grace=1.5)
            self._runs.pop(key, None)
            remove_quietly(staged.prompt_path)
            if staged.attachments_dir is not None:
                shutil.rmtree(staged.attachments_dir, ignore_errors=True)
            self._publish(owner_id, session_id, _END_EVENT)

    def _effective_model(self, model: str | None) -> str | None:
        requested = str(model or "").strip()
        return requested.split("/", 1)[1] if requested.startswith("freebuff/") else requested or None

    async def _wait_ready(self, run: _Run, installation: FreebuffInstallation) -> None:
        process = run.process
        if process is None:
            raise BackendUnavailableError("Freebuff process did not start")
        startup = max(5.0, float(self._cfg("startup_seconds", 90.0)))
        deadline = time.monotonic() + startup
        stable_since: float | None = None
        while time.monotonic() < deadline:
            chunk = await process.read(0.5)
            if chunk:
                run.screen.feed(chunk)
                stable_since = None
            else:
                stable_since = stable_since or time.monotonic()
            if run.screen.login_required:
                raise BackendAuthError()
            error = run.screen.error()
            if error is not None:
                self._raise_screen_error(*error)
            if not process.alive and process.exit_code is not None:
                raise BackendUnavailableError(
                    f"Freebuff exited during startup (code {process.exit_code})"
                )
            if run.screen.prompt_visible and stable_since is not None:
                return
            if stable_since is not None and time.monotonic() - stable_since >= 3.0:
                return
        raise BackendTimeoutError("Freebuff TUI did not become ready before the startup deadline")

    async def _wait_for_answer(
        self,
        run: _Run,
        installation: FreebuffInstallation,
        *,
        started: float,
        since: float,
        should_continue: Any | None,
    ) -> tuple[str, ChatRef | None]:
        process = run.process
        if process is None:
            raise BackendUnavailableError("Freebuff process is missing")
        timeout = max(60.0, float(self._cfg("timeout_seconds", 3600.0)))
        idle_seconds = max(3.0, float(self._cfg("idle_seconds", 25.0)))
        next_poll = 0.0
        last_status = ""
        while True:
            now = time.monotonic()
            if now - started > timeout:
                raise BackendTimeoutError("Freebuff task exceeded the configured timeout")
            if should_continue is not None:
                try:
                    allowed = await should_continue()
                except Exception:
                    allowed = True
                if not allowed:
                    run.cancelled = True
                    raise TaskCancelledError("task cancelled during Freebuff execution")

            chunk = await process.read(0.4)
            if chunk:
                run.screen.feed(chunk)
                if run.screen.login_required:
                    raise BackendAuthError()
                error = run.screen.error()
                if error is not None:
                    self._raise_screen_error(*error)
                if run.screen.busy and last_status != "busy":
                    last_status = "busy"
                    self._publish(
                        run.owner_id,
                        run.session_id,
                        status_event(run.session_id, "busy", "Freebuff يعمل على المهمة."),
                    )

            if now >= next_poll:
                next_poll = now + 2.0
                if run.chat is None:
                    run.chat = await asyncio.to_thread(
                        find_latest_chat,
                        installation.config_dir,
                        since=since,
                        contains=run.prompt_marker or None,
                    )
                if run.chat is not None and not run.screen.busy and run.screen.idle_for(idle_seconds, now=now):
                    text = await self._text_from_chat(run.chat)
                    if text:
                        return text, run.chat

            if not process.alive and process.exit_code is not None:
                if run.chat is not None:
                    text = await self._text_from_chat(run.chat)
                    if text:
                        return text, run.chat
                text = await self._export(run, installation, since=since)
                if text:
                    return text, run.chat
                raise BackendProtocolError(
                    f"Freebuff exited (code {process.exit_code}) without a retrievable answer"
                )

            if run.screen.idle_for(idle_seconds) and not run.screen.busy and now - started > 15.0:
                if run.chat is None:
                    run.chat = await asyncio.to_thread(
                        find_latest_chat,
                        installation.config_dir,
                        since=since,
                        contains=run.prompt_marker or None,
                    )
                if run.chat is not None:
                    text = await self._text_from_chat(run.chat)
                    if text:
                        return text, run.chat
                text = await self._export(run, installation, since=since)
                if text:
                    return text, run.chat
                tail = clean_text(run.screen.tail(25), max_chars=1200)
                raise BackendProtocolError(
                    "Freebuff returned no retrievable assistant message"
                    + (f" (screen tail: {tail[-400:]})" if tail else "")
                )

    async def _text_from_chat(self, chat: ChatRef) -> str:
        try:
            messages = await asyncio.to_thread(load_messages, chat)
        except ValueError as exc:
            self.log.info("freebuff chat store unreadable: %s", type(exc).__name__)
            return ""
        return clean_text(
            answer_text(messages),
            max_chars=int(self._cfg("max_output_chars", 120_000)),
        )

    async def _export(
        self,
        run: _Run,
        installation: FreebuffInstallation,
        *,
        since: float,
    ) -> str:
        process = run.process
        if process is None or not process.alive:
            return ""
        config_dir = installation.config_dir
        before = await asyncio.to_thread(self._workspace_snapshot)
        export_seconds = max(5.0, float(self._cfg("export_seconds", 60.0)))
        for command in ("/export json\r", "/export\r"):
            await process.write(command)
            deadline = time.monotonic() + export_seconds
            while time.monotonic() < deadline:
                chunk = await process.read(0.5)
                if chunk:
                    run.screen.feed(chunk)
                candidate = await asyncio.to_thread(newest_export, self.workspace, since=since)
                if candidate is None:
                    continue
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved in before:
                    continue
                try:
                    text = await asyncio.to_thread(
                        read_export_file,
                        candidate,
                        max_chars=int(self._cfg("max_output_chars", 120_000)),
                    )
                except ValueError:
                    text = ""
                remove_quietly(candidate)
                if text:
                    return text
        self.log.info("freebuff export fallback produced no answer (config=%s)", config_dir)
        return ""

    def _workspace_snapshot(self) -> set[Path]:
        found: set[Path] = set()
        root = self.workspace
        if not root.is_dir():
            return found
        for depth_root, dirs, files in os.walk(root):
            try:
                depth = len(Path(depth_root).relative_to(root).parts)
            except ValueError:
                continue
            dirs[:] = [name for name in dirs if name not in _IGNORED_DIRS and not name.startswith(".")]
            if depth >= 2:
                dirs[:] = []
            for name in files:
                if Path(name).suffix.casefold() not in {".json", ".md"}:
                    continue
                try:
                    found.add((Path(depth_root) / name).resolve())
                except OSError:
                    continue
        return found

    def _raise_screen_error(self, kind: str, message: str) -> None:
        if kind == "auth":
            raise BackendAuthError(message)
        if kind == "rate_limited":
            raise FreebuffQuotaError(message, status_code=429)
        if kind == "quota":
            raise FreebuffQuotaError(message, status_code=503)
        raise BackendProtocolError(message)

    async def abort_session(self, session_id: str, owner_id: str | None = None) -> bool:
        run = self._runs.get(self._session_key(owner_id, session_id)) if owner_id else None
        if run is None:
            matches = [
                candidate
                for candidate in self._runs.values()
                if candidate.process is not None and candidate.session_id == session_id
            ]
            if owner_id:
                matches = [candidate for candidate in matches if candidate.owner_id == owner_id]
            run = matches[0] if len(matches) == 1 else None
        if run is None:
            return False
        run.cancelled = True
        if run.process is not None:
            await run.process.terminate(grace=1.0)
        self._publish(run.owner_id, session_id, idle_event(session_id))
        return True

    async def close(self) -> None:
        for run in list(self._runs.values()):
            if run.process is not None:
                await run.process.terminate(grace=0.5)
        self._runs.clear()

    def _audit(self, event: str, outcome: str, owner_id: str, details: dict[str, Any]) -> None:
        if self.audit_write is None:
            return
        try:
            self.audit_write(event, outcome, actor_id=owner_id, details=details)
        except Exception:
            self.log.debug("freebuff audit write failed", exc_info=True)
