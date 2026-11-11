"""Documentation-as-code checks for the current project documentation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

REQUIRED_DOCS = {
    "AGENTS.md": ("OpenCode", "Freebuff", "Verification", "Releases"),
    "docs/PROJECT_STATUS.md": ("Current state", "Verification evidence", "Known limitations"),
    "docs/ARCHITECTURE.md": ("Runtime topology", "Backend abstraction", "Security boundaries"),
    "docs/ROADMAP.md": ("Now", "Next", "Later"),
    "docs/DECISIONS.md": ("Decision Log", "ADR 0001"),
    "docs/SESSION_LOG.md": ("2026-11-11", "Freebuff", "Verification"),
    "docs/README.md": ("Documentation Index", "Architecture", "Operations"),
}


class DocumentationAsCodeTests(unittest.TestCase):
    def test_required_docs_exist_and_cover_their_purpose(self) -> None:
        for relative, sections in REQUIRED_DOCS.items():
            path = ROOT / relative
            self.assertTrue(path.is_file(), f"missing documentation: {relative}")
            text = path.read_text(encoding="utf-8")
            for section in sections:
                self.assertIn(section, text, f"{relative} is missing {section!r}")

    def test_documentation_index_links_resolve(self) -> None:
        index = DOCS / "README.md"
        text = index.read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+\.md)\)", text)
        self.assertGreaterEqual(len(links), 10)
        for link in links:
            target = (index.parent / link).resolve()
            self.assertTrue(target.is_file(), f"broken documentation link: {link}")


if __name__ == "__main__":
    unittest.main()
