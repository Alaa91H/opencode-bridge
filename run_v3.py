"""Production entrypoint for OpenCode Bridge V3."""

from __future__ import annotations

import asyncio
import os

import bot as core
import v3_plugin

_original_post_init = core.post_init
_original_post_shutdown = core.post_shutdown


async def post_init(app) -> None:
    core.TaskService = v3_plugin.V3TaskService
    core.DEFAULT_AGENT = os.environ.get("OPENCODE_AGENT", "development-agent")
    await _original_post_init(app)
    await v3_plugin.install(app)


async def post_shutdown(app) -> None:
    await v3_plugin.close()
    await _original_post_shutdown(app)


core.post_init = post_init
core.post_shutdown = post_shutdown


if __name__ == "__main__":
    asyncio.run(core.main())
