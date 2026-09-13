"""Production entrypoint for OpenCode Bridge V3."""

from __future__ import annotations

import asyncio
import logging
import os

from adaptive_workers import choose_worker_limit

log = logging.getLogger("opencode_bridge.bootstrap")
configured_workers = max(1, min(int(os.environ.get("AGENT_TASK_WORKERS", "2")), 8))
adaptive_workers = os.environ.get("AGENT_ADAPTIVE_WORKERS", "1").strip().lower() not in {"0", "false", "no", "off"}
if adaptive_workers:
    safe_workers = choose_worker_limit(configured_workers)
    os.environ["AGENT_TASK_WORKERS"] = str(safe_workers)
    log.info("adaptive worker sizing configured=%s active=%s", configured_workers, safe_workers)

import bot as core
import ci_plugin
import resource_commands
import v3_plugin

_original_post_init = core.post_init
_original_post_shutdown = core.post_shutdown


async def post_init(app) -> None:
    core.TaskService = v3_plugin.V3TaskService
    core.DEFAULT_AGENT = os.environ.get("OPENCODE_AGENT", "development-agent")
    await _original_post_init(app)
    await v3_plugin.install(app)
    await ci_plugin.install(app)
    await resource_commands.install(app)


async def post_shutdown(app) -> None:
    await ci_plugin.close()
    await v3_plugin.close()
    await _original_post_shutdown(app)


core.post_init = post_init
core.post_shutdown = post_shutdown


if __name__ == "__main__":
    asyncio.run(core.main())
