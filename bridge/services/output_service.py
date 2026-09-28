"""Long-output persistence and Telegram-neutral delivery contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from bridge.domain.tasks.outputs import LongOutputComposer, OutputManifest


class OutputService:
    def __init__(self, *, store_artifact: Callable[[str, bytes, str], Awaitable[str]],
                 composer: LongOutputComposer | None = None) -> None:
        self.store_artifact = store_artifact
        self.composer = composer or LongOutputComposer()

    async def prepare(self, task_id: str, content: str, *, markdown: bool = True,
                      paginate: bool = False) -> tuple[OutputManifest, str]:
        manifest, payload = self.composer.compose(
            task_id, content, markdown=markdown, paginate=paginate)
        location = await self.store_artifact(
            manifest.full.name, payload, manifest.full.media_type)
        return manifest, location
