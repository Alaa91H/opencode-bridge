"""Telegram-facing workspace rendering helpers."""

from __future__ import annotations

from typing import Any


def render_workspace_status(status: Any) -> str:
    state = "dirty" if status.dirty else "clean"
    suffix = f"\n{status.summary}" if getattr(status, "summary", "") else ""
    return f"{status.slug}\nBranch: {status.branch}\nState: {state}{suffix}"


def render_workspace_selection(status: Any) -> str:
    state = "dirty" if status.dirty else "clean"
    return f"Active repository: {status.slug}\nBranch: {status.branch}\nState: {state}"


def render_workspace_list(items: tuple[Any, ...]) -> str:
    if not items:
        return "اضبط GITHUB_ALLOWED_REPOS في .env أولًا."
    lines = ["Allowed repositories:"]
    for item in items:
        labels = []
        if item.local:
            labels.append("local")
        if item.active:
            labels.append("active")
        lines.append(f"• {item.slug}" + (f" — {', '.join(labels)}" if labels else ""))
    return "\n".join(lines)


def render_workspace_sync(status: Any) -> str:
    note = (
        "Fetched remote state; local edits were preserved."
        if status.dirty
        else "Repository synchronized safely."
    )
    return f"{note}\n{status.slug} — {status.branch}\n{status.summary}"
