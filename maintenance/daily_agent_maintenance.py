#!/usr/bin/env python3
"""Daily autonomous model selection and post-maintenance agent audit."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from audit_log import AuditLogger
from model_manager import ModelManager
from opencode_client import OpenCodeClient, extract_text_response
from session_store import SessionStore

RUNTIME_DIR = BRIDGE_DIR / "runtime"
REPORT_PATH = RUNTIME_DIR / "agent-maintenance-latest.md"
STATE_PATH = RUNTIME_DIR / "agent-maintenance-latest.json"
UTC = timezone.utc


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(BRIDGE_DIR / ".env")


async def run() -> dict[str, Any]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    store = SessionStore(BRIDGE_DIR / "sessions.db")
    await store.init()
    client = OpenCodeClient(
        host=os.environ.get("OPENCODE_HOST", "127.0.0.1"),
        port=int(os.environ.get("OPENCODE_PORT", "4096")),
        password=os.environ.get("OPENCODE_PASSWORD") or os.environ.get("OPENCODE_SERVER_PASSWORD"),
    )
    audit = AuditLogger(RUNTIME_DIR / "audit.jsonl")
    manager = ModelManager(
        client=client,
        store=store,
        audit=audit,
        fallback_model=os.environ.get("OPENCODE_DEFAULT_MODEL", "opencode/muse-spark-1.3-contributor-free"),
        sync_seconds=86400,
        pin_default_model=False,
    )
    manager.scout_web_research = True
    session_id: str | None = None
    try:
        healthy = False
        for _ in range(20):
            if await client.health_check():
                healthy = True
                break
            await asyncio.sleep(3)
        if not healthy:
            raise RuntimeError("OpenCode did not become healthy before the daily agent task")

        selection = await manager.scout_once()
        model = selection.get("selected_model") if isinstance(selection, dict) else await manager.best_available()
        if not isinstance(model, str) or not model:
            raise RuntimeError("no verified zero-cost OpenCode Zen model is currently available")
        variant = manager.variant_for_model(model)
        agent = (
            selection.get("selected_agent")
            if isinstance(selection, dict) and isinstance(selection.get("selected_agent"), str)
            else manager.current_agent(os.environ.get("AGENT_SCOUT_PREFERRED_AGENT", "development-agent"))
        )

        created = await client.create_session(title="Daily OpenCode Bridge maintenance audit")
        raw_id = created.get("id") or created.get("sessionId")
        if not isinstance(raw_id, str) or not raw_id:
            raise RuntimeError("OpenCode did not return a maintenance session ID")
        session_id = raw_id
        prompt = (
            "Perform a concise post-maintenance audit for the OpenCode Bridge host. "
            "This is a read-only verification task: inspect the active repository status, recent maintenance report, "
            "service health information available to you, and obvious configuration drift. Do not install packages, "
            "do not build software, do not modify Git history, do not delete files, and do not reboot the host. "
            "Report only concrete findings, whether the deployment appears healthy, and any action that still needs "
            "the owner. Mention the selected model and reasoning variant only if relevant."
        )
        try:
            response = await client.send_prompt(
                session_id,
                prompt,
                model=model,
                agent=agent,
                variant=variant,
            )
        except httpx.HTTPStatusError as exc:
            if not variant or exc.response.status_code not in {400, 404, 422}:
                raise
            response = await client.send_prompt(session_id, prompt, model=model, agent=agent, variant=None)
            variant = None

        audit_text = extract_text_response(response).strip() or "لم يُرجع الوكيل ملخصًا نصيًا."
        now = datetime.now(UTC).isoformat()
        report = (
            "# Daily autonomous maintenance audit\n\n"
            f"- Time: {now}\n"
            f"- Selected free model: `{model}`\n"
            f"- Maximum supported variant: `{variant or 'default'}`\n"
            f"- Agent: `{agent}`\n\n"
            "## Agent audit\n\n"
            f"{audit_text}\n"
        )
        REPORT_PATH.write_text(report, encoding="utf-8")
        os.chmod(REPORT_PATH, 0o640)
        payload = {
            "timestamp": now,
            "selected_model": model,
            "selected_variant": variant,
            "selected_agent": agent,
            "selection_method": selection.get("selection_method") if isinstance(selection, dict) else "catalog_fallback",
        }
        STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(STATE_PATH, 0o640)
        audit.write("daily_autonomous_maintenance", "success", details=payload)
        return payload
    finally:
        if session_id:
            try:
                await client.delete_session(session_id)
            except Exception:
                pass
        await client.close()
        await store.close()


def main() -> int:
    try:
        result = asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "success", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
