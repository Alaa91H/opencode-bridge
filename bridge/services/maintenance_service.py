"""Framework-independent maintenance-report access."""

from __future__ import annotations

from pathlib import Path


class MaintenanceReportService:
    def __init__(self, report_path: Path, *, max_chars: int = 3500) -> None:
        self.report_path = report_path
        self.max_chars = max(256, int(max_chars))

    def latest(self) -> str | None:
        if not self.report_path.is_file():
            return None
        report = self.report_path.read_text(encoding="utf-8").strip()
        if not report:
            return ""
        if len(report) <= self.max_chars:
            return report
        return report[: self.max_chars] + "\n… تم اختصار التقرير."
