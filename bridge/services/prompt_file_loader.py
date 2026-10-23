"""Prompt intake from text files (.txt, .md, .rst, .py) for agent execution."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("opencode_bridge.prompt_file")


SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".json", ".yaml", ".yml",
    ".csv", ".log", ".conf", ".ini", ".sh", ".bash",
}


class PromptFileLoader:
    """Load large/complex/multi-file prompts from files or directories."""

    def __init__(self, max_size_bytes: int = 5 * 1024 * 1024) -> None:
        self.max_size_bytes = max_size_bytes

    def load(self, path: Path | str) -> str:
        p = Path(path)
        if p.is_dir():
            parts: list[str] = []
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                    try:
                        text = child.read_text(encoding="utf-8")
                        if len(text) > 100000:
                            text = text[:100000] + "\n... [truncated]"
                        parts.append(f"===== {child.name} =====\n{text}")
                    except Exception:
                        pass
            return "\n\n".join(parts)
        try:
            data = p.read_bytes()
            if len(data) > self.max_size_bytes:
                return (data[:self.max_size_bytes].decode("utf-8", errors="ignore") +
                        "\n... [file truncated due to size]")
            return p.read_text(encoding="utf-8")
        except Exception as exc:
            raise ValueError(f"cannot read prompt file {p}: {exc}")

    def detect_format(self, text: str) -> str:
        if text.startswith("# ") or text.startswith("## "):
            return "markdown"
        if "---" in text[:200] and (text.startswith("---") or text.startswith("\n---")):
            return "yaml/yml"
        return "text"


def prepare_agent_execution(
    prompt_text: str,
    files: list[Path] | None = None,
    agent_service: Any | None = None,
    schedule_service: Any | None = None,
) -> dict[str, Any]:
    """Integrate prompt file + agent + scheduling for full execution."""
    loader = PromptFileLoader()
    combined = prompt_text
    if files:
        for f in files:
            combined += "\n\n--- FILE: {} ---\n{}".format(f.name, loader.load(f))
    result = {"prompt": combined, "files_processed": len(files or [])}
    # If agent service available: pass combined prompt with context
    if agent_service is not None and hasattr(agent_service, "ensure_session"):
        result["agent_ready"] = True
    # If scheduling needed: allow scheduling this execution
    if schedule_service is not None:
        result["can_schedule"] = True
    return result
