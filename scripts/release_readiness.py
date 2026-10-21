#!/usr/bin/env python3
"""Fail-closed OpenCode Bridge 2.0 release-readiness evidence gate."""

from __future__ import annotations

import json
from pathlib import Path

REQUIRED = (
    "ci", "python_matrix", "migration", "backup_restore", "crash_recovery",
    "schedule_recovery", "duplicate_delivery", "file_streaming",
    "local_bot_large_file", "long_prompt", "multi_workspace",
    "dependency_security", "release_rollback", "documentation",
)


def validate(path: Path, request_path: Path = Path("soak-request-2.0.json")) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    failures = [name for name in REQUIRED if data.get(name) is not True]
    soak = data.get("soak", {})
    hours = float(soak.get("duration_hours", 0))
    if not 24 <= hours <= 72:
        failures.append("soak_24_72h")
    if soak.get("completed") is not True:
        failures.append("soak_completed")
    if request_path.is_file():
        request = json.loads(request_path.read_text(encoding="utf-8"))
        expected_request_id = str(request.get("request_id", ""))
        if not expected_request_id or soak.get("request_id") != expected_request_id:
            failures.append("soak_request_id")
    if data.get("high_critical_vulnerabilities_accepted_or_zero") is not True:
        failures.append("dependency_vulnerability_acceptance")
    return failures


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    failures = validate(args.evidence)
    if failures:
        raise SystemExit("release blocked; missing/failed evidence: " + ", ".join(failures))
    print("OpenCode Bridge 2.0 release evidence is complete")


if __name__ == "__main__":
    main()
