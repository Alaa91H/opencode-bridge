"""Telegram rendering for GitHub CI status."""

from __future__ import annotations


def render_ci(status) -> str:
    icons = {
        "success": "✅",
        "failure": "❌",
        "pending": "⏳",
        "not_found": "⚪",
        "timeout": "⌛",
        "unknown": "❔",
    }
    lines = [f"{icons.get(status.state, 'ℹ️')} CI: {status.state}"]
    if status.workflow:
        lines.append(f"Workflow: {status.workflow}")
    if status.branch:
        lines.append(f"Branch: {status.branch}")
    if status.head_sha:
        lines.append(f"Commit: {status.head_sha[:12]}")
    if status.conclusion:
        lines.append(f"Conclusion: {status.conclusion}")
    if status.failed_steps:
        lines.append("Failed steps:")
        lines.extend(f"• {item}" for item in status.failed_steps[:8])
    if status.html_url:
        lines.append(status.html_url)
    return "\n".join(lines)
