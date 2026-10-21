#!/usr/bin/env python3
"""Reproducible OpenCode Bridge v2 baseline inventory and micro-benchmark.

This script is intentionally standard-library-only so it can run in CI before
runtime dependencies are installed and on production hosts during diagnostics.
It does not read .env secrets; it only scans source/configuration names.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import re
import resource
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

PYTHON_ENV_CALLS = {"getenv"}
SHELL_ENV_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]+)(?::[-+?][^}]*)?\}")
CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
BOT_COMMAND_RE = re.compile(r"BotCommand\(\s*[\"']([^\"']+)[\"']")
COMMAND_HANDLER_RE = re.compile(r"CommandHandler\(\s*[\"']([^\"']+)[\"']")
ENV_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=", re.M)


def _python_env_names(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return names

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
                and value.attr == "environ"
            ):
                key = node.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.add(key.value)
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr in PYTHON_ENV_CALLS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ) or (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
                and func.value.attr == "environ"
                and func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)
    return names


def _scan_environment() -> dict[str, list[str]]:
    documented: set[str] = set()
    for env_file in sorted(ROOT.glob(".env*.example")):
        documented.update(ENV_LINE_RE.findall(env_file.read_text(encoding="utf-8")))

    python_refs: set[str] = set()
    for path in sorted(ROOT.glob("*.py")):
        python_refs.update(_python_env_names(path))
    for path in sorted((ROOT / "maintenance").glob("*.py")):
        python_refs.update(_python_env_names(path))

    shell_refs: set[str] = set()
    for directory in (ROOT / "maintenance", ROOT / "deploy"):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file():
                try:
                    shell_refs.update(SHELL_ENV_RE.findall(path.read_text(encoding="utf-8")))
                except UnicodeDecodeError:
                    pass

    return {
        "documented": sorted(documented),
        "python_references": sorted(python_refs),
        "shell_references": sorted(shell_refs),
    }


def _scan_sqlite_tables() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name in CREATE_TABLE_RE.findall(text):
            rows.append({"table": name, "source": path.name})
    return sorted(rows, key=lambda item: (item["table"], item["source"]))


def _scan_commands() -> list[str]:
    commands: set[str] = set()
    for path in sorted(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        commands.update(BOT_COMMAND_RE.findall(text))
        commands.update(COMMAND_HANDLER_RE.findall(text))
    return sorted(commands)


def _unit_summary(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in {
            "Description",
            "ExecStart",
            "OnCalendar",
            "RandomizedDelaySec",
            "WorkingDirectory",
            "EnvironmentFile",
            "Restart",
            "WantedBy",
            "Requires",
            "After",
        }:
            fields[key] = value
    return {"path": str(path.relative_to(ROOT)), "fields": fields}


def _scan_units() -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for directory in (ROOT / "deploy", ROOT / "maintenance"):
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix in {".service", ".timer", ".target"}:
                units.append(_unit_summary(path))
    return units


def _safe_eval_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add, ast.Sub)):
        left = _safe_eval_int(node.left)
        right = _safe_eval_int(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        return left - right
    return None


def _attachment_defaults() -> dict[str, int]:
    wanted = {
        "DEFAULT_MAX_ATTACHMENT_BYTES",
        "DEFAULT_MAX_ATTACHMENTS_PER_TASK",
        "DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES",
        "DEFAULT_MAX_OUTGOING_FILES",
    }
    tree = ast.parse((ROOT / "attachments.py").read_text(encoding="utf-8"))
    values: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in wanted:
                value = _safe_eval_int(node.value)
                if value is not None:
                    values[name] = value
    return values


def _opencode_policy() -> dict[str, Any]:
    config = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
    permission = config.get("permission", {})
    bash = permission.get("bash", {}) if isinstance(permission, dict) else {}
    return {
        "server": config.get("server"),
        "default_agent": config.get("default_agent"),
        "model": config.get("model"),
        "share": config.get("share"),
        "snapshot": config.get("snapshot"),
        "permission_categories": sorted(permission) if isinstance(permission, dict) else [],
        "bash_denied_patterns": sorted(
            key for key, value in bash.items() if key != "*" and value == "deny"
        ) if isinstance(bash, dict) else [],
        "read_denied_patterns": sorted(
            key for key, value in permission.get("read", {}).items()
            if key != "*" and value == "deny"
        ) if isinstance(permission, dict) and isinstance(permission.get("read"), dict) else [],
        "edit_denied_patterns": sorted(
            key for key, value in permission.get("edit", {}).items()
            if key != "*" and value == "deny"
        ) if isinstance(permission, dict) and isinstance(permission.get("edit"), dict) else [],
    }


def inventory() -> dict[str, Any]:
    telegram_service = ROOT / "deploy" / "opencode-bridge-telegram.service"
    service_text = telegram_service.read_text(encoding="utf-8")
    exec_start = next(
        (line.split("=", 1)[1] for line in service_text.splitlines() if line.startswith("ExecStart=")),
        "",
    )
    tests = sorted(path.name for path in (ROOT / "tests").glob("test_*.py"))
    return {
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "production_entrypoint": exec_start,
        "python_modules": sorted(path.name for path in ROOT.glob("*.py")),
        "test_files": tests,
        "telegram_commands": _scan_commands(),
        "environment": _scan_environment(),
        "sqlite_tables": _scan_sqlite_tables(),
        "systemd_units": _scan_units(),
        "attachment_defaults": _attachment_defaults(),
        "opencode_policy": _opencode_policy(),
    }


def _rss_kib() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if platform.system() == "Darwin":
        return value // 1024
    return value


def benchmark() -> dict[str, Any]:
    cpu_loops = 48
    block = b"opencode-bridge-baseline" * 65536
    rss_before = _rss_kib()
    start = time.perf_counter()
    digest = hashlib.sha256()
    for _ in range(cpu_loops):
        digest.update(block)
    cpu_seconds = max(time.perf_counter() - start, 1e-9)
    processed_mib = (len(block) * cpu_loops) / (1024 * 1024)

    insert_samples: list[float] = []
    select_samples: list[float] = []
    row_count = 5000
    with tempfile.TemporaryDirectory(prefix="opencode-baseline-") as temp:
        db_path = Path(temp) / "baseline.db"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE INDEX idx_items_owner ON items(owner)")
            for _ in range(3):
                connection.execute("DELETE FROM items")
                connection.commit()
                started = time.perf_counter()
                connection.executemany(
                    "INSERT INTO items(id, owner, payload) VALUES (?, ?, ?)",
                    ((i, f"owner-{i % 50}", "x" * 256) for i in range(row_count)),
                )
                connection.commit()
                insert_samples.append(time.perf_counter() - started)

                started = time.perf_counter()
                for i in range(2000):
                    connection.execute("SELECT payload FROM items WHERE id = ?", (i % row_count,)).fetchone()
                select_samples.append(time.perf_counter() - started)
        finally:
            connection.close()

    rss_after = _rss_kib()
    insert_median = statistics.median(insert_samples)
    select_median = statistics.median(select_samples)
    return {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "rss": {
            "before_kib": rss_before,
            "after_kib": rss_after,
            "delta_kib": max(0, rss_after - rss_before),
        },
        "cpu_hash": {
            "processed_mib": round(processed_mib, 3),
            "seconds": round(cpu_seconds, 6),
            "mib_per_second": round(processed_mib / cpu_seconds, 3),
            "digest_prefix": digest.hexdigest()[:16],
        },
        "sqlite": {
            "rows": row_count,
            "insert_median_seconds": round(insert_median, 6),
            "insert_rows_per_second": round(row_count / max(insert_median, 1e-9), 3),
            "point_selects": 2000,
            "select_median_seconds": round(select_median, 6),
            "point_selects_per_second": round(2000 / max(select_median, 1e-9), 3),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--benchmark-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.inventory_only and args.benchmark_only:
        parser.error("--inventory-only and --benchmark-only are mutually exclusive")

    if args.inventory_only:
        payload: dict[str, Any] = {"inventory": inventory()}
    elif args.benchmark_only:
        payload = {"benchmark": benchmark()}
    else:
        payload = {"inventory": inventory(), "benchmark": benchmark()}

    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
