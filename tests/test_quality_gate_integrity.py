"""Regression tests for the strict hygiene gate in ``scripts/quality.sh``.

The bug
-------
The Code Quality workflow has a step that fails the job when the gate script or
the workflow itself contains an exit-zero fallback operator. ``scripts/quality.sh``
carried three of them: two in the report-only mypy block and one in the vulture
helper, which captures output and is expected to see a non-zero exit status. Both
files also spelled the pattern out inside the comments that documented why the
gate is strict. The meta-check could therefore never pass, so the hygiene job was
red on every push and on every matrix Python version.

This assertion lives in the local suite rather than only in CI on purpose: the
workflow step catches the regression minutes later on a remote runner, whereas
this fails in the same command the developer already runs.

The second block guards the same failure class in the Python tree: the newest CI
matrix entry runs with ``PYTHONWARNINGS=error::DeprecationWarning``, so a single
use of a spelling that is deprecated in 3.14 turns that one job red while the
3.12 and 3.13 jobs stay green.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUALITY_SCRIPT = ROOT / "scripts" / "quality.sh"
QUALITY_WORKFLOW = ROOT / ".github" / "workflows" / "quality-gate.yml"

# The exact pattern the workflow step greps for. Kept identical on purpose: if
# the workflow ever widens it, this test must widen with it.
EXIT_ZERO_FALLBACK = re.compile(r"\|\|\s*true")

# Deprecated in Python 3.14 and removed in 3.16.
REMOVED_ALIASES = ("asyncio.iscoroutinefunction", "asyncio.coroutine")

SELF = Path(__file__).resolve()


def _gated_files() -> list[Path]:
    return [QUALITY_SCRIPT, QUALITY_WORKFLOW]


def _python_sources() -> list[Path]:
    """Return every first-party Python file the alias scan must cover."""
    sources = [ROOT / "bot.py"]
    for package in ("bridge", "tests"):
        sources.extend(sorted((ROOT / package).rglob("*.py")))
    return [
        path for path in sources if path.is_file() and path.resolve() not in {SELF}
    ]


class HygieneGateEscapeTests(unittest.TestCase):
    def test_gate_and_workflow_have_no_exit_zero_fallback(self) -> None:
        offenders: list[str] = []
        for path in _gated_files():
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                if EXIT_ZERO_FALLBACK.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(
            offenders,
            [],
            "an exit-zero fallback makes a check incapable of failing the job; "
            "capture the status into a variable instead",
        )

    def test_workflow_still_enforces_the_escape_check(self) -> None:
        workflow = QUALITY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Confirm the gate has no advisory escapes", workflow)
        self.assertIn("advisory escape found", workflow)


class RemovedAliasTests(unittest.TestCase):
    def test_first_party_python_avoids_removed_asyncio_aliases(self) -> None:
        offenders: list[str] = []
        for path in _python_sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for alias in REMOVED_ALIASES:
                if alias in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {alias}")
        self.assertEqual(
            offenders,
            [],
            "CI runs the newest Python with DeprecationWarning as an error, so "
            "this spelling fails only the newest matrix job",
        )


if __name__ == "__main__":
    unittest.main()
