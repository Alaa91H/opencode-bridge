"""Keep the live documentation set aligned with the current architecture."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class DocumentationAsCodeTests(unittest.TestCase):
    def test_required_operational_docs_exist(self) -> None:
        required = (
            "ARCHITECTURE.md",
            "PROJECT_STATUS.md",
            "ROADMAP.md",
            "DECISIONS.md",
            "SESSION_LOG.md",
            "freebuff-integration-ar.md",
            "freebuff-integration-en.md",
            "adr/0001-freebuff-cli-backend.md",
        )
        for name in required:
            self.assertTrue((DOCS / name).is_file(), name)

    def test_all_relative_markdown_links_exist(self) -> None:
        failures: list[str] = []
        for source in [DOCS / "README.md", ROOT / "README.md"]:
            text = source.read_text(encoding="utf-8")
            for link in re.findall(r"\[[^\]]+\]\(([^)]+\.md)\)", text):
                if link.startswith(("http://", "https://", "#")):
                    continue
                target = (source.parent / link).resolve()
                if not target.is_file():
                    failures.append(f"{source.relative_to(ROOT)} -> {link}")
        self.assertEqual(failures, [])

    def test_obsolete_stage_and_planning_docs_are_removed(self) -> None:
        obsolete = list(DOCS.glob("t[0-9][0-9]-*.md"))
        obsolete += list(DOCS.glob("2.0-*.md"))
        obsolete += list(DOCS.glob("opencode-bridge-2.0-*.md"))
        obsolete += list(DOCS.glob("autonomous-operations-*.md"))
        self.assertEqual(obsolete, [], "remove superseded documentation instead of keeping it")


if __name__ == "__main__":
    unittest.main()
