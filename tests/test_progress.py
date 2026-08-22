from __future__ import annotations

import unittest

from progress import ProgressStore, render_persisted_activity, render_progress, serialize_progress, summarize_agent_event


class ProgressEventTests(unittest.TestCase):
    def test_tool_event_is_summarized_without_inputs_or_outputs(self) -> None:
        event = {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "cat /secret"},
                        "output": "very-secret-output",
                    },
                }
            },
        }
        summary = summarize_agent_event(event)
        self.assertEqual(summary, ("tool_completed", "خلص أمرًا على الخادم.", "success"))
        self.assertNotIn("secret", summary[1])

    def test_plan_progress_counts_only_statuses(self) -> None:
        event = {
            "type": "todo.updated",
            "properties": {
                "todos": [
                    {"content": "private task one", "status": "completed"},
                    {"content": "private task two", "status": "in_progress"},
                    {"content": "private task three", "status": "pending"},
                ]
            },
        }
        summary = summarize_agent_event(event)
        self.assertEqual(summary, ("plan", "تحديث خطة التنفيذ: 1/3 خطوة مكتملة، و1 قيد العمل.", "info"))

    def test_progress_render_and_persistence_are_compact(self) -> None:
        store = ProgressStore()
        progress = store.start(17, "42", 99)
        store.record(17, "tool_running", "عم ينفّذ أمرًا على الخادم.")
        text = render_progress(progress)
        activity = serialize_progress(progress)
        persisted = render_persisted_activity(17, "running", tuple(activity))
        self.assertIn("تقدم المهمة #17", text)
        self.assertNotIn("/trace", text)
        self.assertNotIn("/abort", text)
        self.assertIn("سجل المهمة #17", persisted)
        self.assertEqual(len(activity), 2)
        self.assertTrue(all(set(item) == {"time", "phase", "message", "kind"} for item in activity))


if __name__ == "__main__":
    unittest.main()
