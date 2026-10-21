import unittest
from pathlib import Path


class DocumentationAsCodeTests(unittest.TestCase):
    def test_operations_guide_has_required_sections(self):
        text = Path("docs/2.0-operations-guide-ar.md").read_text()
        required = (
            "Architecture", "DB schema", "Task state machine", "Scheduler semantics",
            "Failure semantics", "Attachment pipeline", "Security profiles",
            "Deployment", "Disaster recovery", "Troubleshooting",
        )
        for section in required:
            self.assertIn(section, text)

    def test_stage_docs_exist_for_major_features(self):
        docs = Path("docs")
        for stage in range(10, 47):
            matches = list(docs.glob(f"t{stage}-*.md"))
            self.assertTrue(matches, f"missing documentation for T{stage}")
