"""Production entrypoint for OpenCode Bridge V3."""

from __future__ import annotations

import asyncio
import logging

import bot as core

log = logging.getLogger("opencode_bridge.bootstrap")
configured_workers = core.SETTINGS.agent.task_workers
adaptive_workers = core.SETTINGS.features.adaptive_workers
log.info(
    "worker admission configured_max=%s adaptive=%s",
    configured_workers,
    adaptive_workers,
)
import ci_plugin
import resource_commands
import v3_plugin
import watchdog_plugin
import workspace_runtime

_original_post_init = core.post_init
_original_post_shutdown = core.post_shutdown


async def post_init(app) -> None:
    # bot.TaskService is the production compatibility service. It already
    # subclasses TaskServiceV3 and adds the stabilized host-resource controller
    # (fast pressure reductions plus gradual recovery).
    core.DEFAULT_AGENT = core.SETTINGS.agent.v3_agent
    await workspace_runtime.install(core, v3_plugin.workspace_store, v3_plugin.workspace_manager)
    await _original_post_init(app)
    await v3_plugin.install(app)
    await ci_plugin.install(app)
    await resource_commands.install(app)
    await watchdog_plugin.install(app)
    await _announce_update_outcome(app)


async def _announce_update_outcome(app) -> None:
    """Tell the owner how the last (detached) Bridge update ended; never block start-up."""
    owner = core.CONTROL_OWNER_ID
    if owner is None:
        return
    from bridge.services.update_report import announce_update_outcome

    async def send(text: str) -> None:
        await app.bot.send_message(chat_id=owner, text=text)

    try:
        await announce_update_outcome(send, core.BRIDGE_DIR / "runtime")
    except Exception:
        log.warning("update outcome announcement failed", exc_info=True)


async def post_shutdown(app) -> None:
    await watchdog_plugin.close()
    await ci_plugin.close()
    await v3_plugin.close()
    await _original_post_shutdown(app)


core.post_init = post_init
core.post_shutdown = post_shutdown


if __name__ == "__main__":
    asyncio.run(core.main())
