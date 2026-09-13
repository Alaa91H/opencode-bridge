from __future__ import annotations

import unittest

from agent_scout import parse_research_model, research_prompt, zen_only_model_ids


class AgentScoutZenScopeTests(unittest.TestCase):
    def test_non_zen_models_are_removed_before_research(self) -> None:
        models = ["other/free-pro", "opencode/basic-free", "opencode/rich-free"]
        self.assertEqual(
            zen_only_model_ids(models),
            ["opencode/basic-free", "opencode/rich-free"],
        )
        prompt = research_prompt(models)
        self.assertNotIn("other/free-pro", prompt)
        self.assertIn("opencode/basic-free", prompt)
        self.assertIn("OpenCode Zen", prompt)

    def test_research_cannot_select_non_zen_candidate(self) -> None:
        models = ["other/free-pro", "opencode/basic-free"]
        self.assertIsNone(parse_research_model('{"model":"other/free-pro"}', models))
        self.assertEqual(
            parse_research_model('{"model":"opencode/basic-free"}', models),
            "opencode/basic-free",
        )


if __name__ == "__main__":
    unittest.main()
