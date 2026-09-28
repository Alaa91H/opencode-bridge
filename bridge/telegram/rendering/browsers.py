"""Unified task/output/schedule browser rendering."""

from __future__ import annotations

from bridge.telegram.rendering.ux_v2 import UXAction, UXPage, UXRenderer


class TelegramBrowsers:
    def __init__(self, renderer: UXRenderer | None = None) -> None:
        self.renderer = renderer or UXRenderer()

    def tasks(self, items: list[str], *, page: int = 0) -> UXPage:
        return self.renderer.page(
            title="Task history", items=items, page=page, breadcrumbs=("Home", "Tasks"))

    def outputs(self, task_id: str, items: list[str], *, page: int = 0) -> UXPage:
        return self.renderer.page(
            title="Outputs", items=items, page=page,
            breadcrumbs=("Home", "Tasks", task_id, "Outputs"),
            actions=(UXAction.DOWNLOAD,), entity_id=task_id)

    def schedules(self, items: list[str], *, page: int = 0) -> UXPage:
        return self.renderer.page(
            title="Schedules", items=items, page=page, breadcrumbs=("Home", "Schedules"))

    def task(self, task_id: str, body: str, *, cancellable: bool = True) -> UXPage:
        actions = [UXAction.RETRY, UXAction.DUPLICATE, UXAction.RERUN, UXAction.DOWNLOAD]
        if cancellable:
            actions.insert(1, UXAction.CANCEL)
        return self.renderer.page(
            title=f"Task {task_id}", items=[body], per_page=1,
            breadcrumbs=("Home", "Tasks", task_id), actions=tuple(actions), entity_id=task_id)
