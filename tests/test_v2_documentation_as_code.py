import re
import unittest
from pathlib import Path


class DocumentationAsCodeTests(unittest.TestCase):
    def test_required_documentation_topics_are_indexed(self):
        text = Path("docs/2.0-documentation-index-ar.md").read_text(encoding="utf-8")
        for topic in (
            "Architecture", "Database & State Machine", "Scheduler Semantics",
            "Failure Semantics", "Attachment & Media Pipeline", "Security Profiles",
            "Deployment & Disaster Recovery", "Troubleshooting",
        ):
            self.assertIn(topic, text)

    def test_all_relative_markdown_links_exist(self):
        index = Path("docs/2.0-documentation-index-ar.md")
        text = index.read_text(encoding="utf-8")
        links = re.findall(r"\[[^]]+\]\(([^)]+\.md)\)", text)
        self.assertGreaterEqual(len(links), 20)
        for link in links:
            self.assertTrue((index.parent / link).is_file(), link)

    def test_every_closed_feature_stage_has_documentation(self):
        docs = {p.name for p in Path("docs").glob("t*-*.md")}
        for stage in range(2, 47):
            if stage == 3:
                # T03 is documented by configuration tests/operations guide.
                continue
            prefix = f"t{stage:02d}-"
            self.assertTrue(any(name.startswith(prefix) for name in docs), prefix)

    def test_policy_requires_feature_docs_in_same_change(self):
        text = Path("docs/2.0-documentation-index-ar.md").read_text(encoding="utf-8")
        self.assertIn("كل feature كبيرة", text)
        self.assertIn("commit/series المرحلة نفسها", text)
