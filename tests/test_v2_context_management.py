import unittest

from bridge.domain.tasks.context import ContextBudget, ContextItem, ContextManager, estimate_tokens


class ContextManagementTests(unittest.TestCase):
    def test_token_estimation_is_conservative_and_stable(self):
        self.assertGreater(estimate_tokens("hello world"), 0)
        self.assertEqual(estimate_tokens(""), 0)

    def test_budget_never_exceeds_input_allowance(self):
        manager = ContextManager(ContextBudget(1000, reserve_output_tokens=200))
        history = [ContextItem("history", "x" * 900, relevance=float(i)) for i in range(10)]
        cp = manager.checkpoint(prompt="do work", history=history)
        self.assertLessEqual(cp.estimated_tokens, 800)
        self.assertGreater(cp.omitted_count, 0)
        self.assertIn("do work", cp.summary)

    def test_required_prompt_survives_context_overflow(self):
        manager = ContextManager(ContextBudget(2000, 100))
        attachments = [ContextItem("attachment", "z" * 1000, relevance=i) for i in range(20)]
        cp = manager.checkpoint(prompt="critical task instruction", attachments=attachments)
        self.assertTrue(any(i.kind == "prompt" for i in cp.selected))
        self.assertGreater(cp.omitted_count, 0)

    def test_history_and_attachment_selection_by_relevance(self):
        manager = ContextManager(ContextBudget(1000))
        items = [
            ContextItem("history", "weather report", relevance=0),
            ContextItem("history", "sqlite migration rollback", relevance=.2),
            ContextItem("attachment", "database migration integrity", relevance=.1),
        ]
        selected = manager.retrieve_history(items, "migration sqlite", limit=2)
        self.assertIn("migration", selected[0].text)

    def test_checkpoint_is_reusable(self):
        manager = ContextManager(ContextBudget(5000))
        first = manager.checkpoint(prompt="continue", history=[ContextItem("history", "prior result")])
        second = manager.checkpoint(prompt="next", history=[ContextItem("checkpoint", first.summary, required=True)])
        self.assertIn("prior result", second.summary)
