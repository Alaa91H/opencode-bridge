"""Production entrypoint for OpenCode Bridge V3."""

from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("opencode_bridge.bootstrap")
configured_workers = max(1, min(int(os.environ.get("AGENT_TASK_WORKERS", "2")), 8))
adaptive_workers = os.environ.get("AGENT_ADAPTIVE_WORKERS", "1").strip().lower() not in {"0", "false", "no", "off"}
log.info(
    "worker admission configured_max=%s adaptive=%s",
    configured_workers,
    adaptive_workers,
)

import bot as core
import ci_plugin
import resource_commands
import v3_plugin
import watchdog_plugin
import workspace_runtime

_original_post_init = core.post_init
_original_post_shutdown = core.post_shutdown


async def post_init(app) -> None:
    # Keep bot.TaskService as the production compatibility service. It already
    # subclasses TaskServiceV3 and adds the stabilized host-resource controller
    # (fast pressure reductions plus gradual recovery). Replacing it here with
    # v3_plugin.V3TaskService would bypass that stabilization in the actual V3
    # entrypoint and make worker admission oscillate around pressure thresholds.
    core.DEFAULT_AGENT = os.environ.get("OPENCODE_AGENT", "development-agent")
    await workspace_runtime.install(core, v3_plugin.workspace_store, v3_plugin.workspace_manager)
    await _original_post_init(app)
    await v3_plugin.install(app)
    await ci_plugin.install(app)
    await resource_commands.install(app)
    await watchdog_plugin.install(app)


async def post_shutdown(app) -> None:
    await watchdog_plugin.close()
    await ci_plugin.close()
    await v3_plugin.close()
    await _original_post_shutdown(app)


core.post_init = post_init
core.post_shutdown = post_shutdown


if __name__ == "__main__":
    asyncio.run(core.main())
