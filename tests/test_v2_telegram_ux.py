import unittest

from bridge.telegram.rendering.browsers import TelegramBrowsers
from bridge.telegram.rendering.ux_v2 import UXAction, UXRenderer


class TelegramUXTests(unittest.TestCase):
    def test_pagination_and_breadcrumbs(self):
        view = UXRenderer().page(
            title="Tasks", items=[str(i) for i in range(20)], page=1, per_page=5,
            breadcrumbs=("Home", "Tasks"))
        self.assertEqual(view.body.splitlines(), ["5", "6", "7", "8", "9"])
        self.assertEqual(view.breadcrumbs, ("Home", "Tasks"))
        callbacks = {b.callback_data for b in view.buttons}
        self.assertIn("ux:page:0:", callbacks)
        self.assertIn("ux:page:2:", callbacks)

    def test_task_has_required_action_buttons(self):
        view = TelegramBrowsers().task("abc", "running")
        callbacks = {b.callback_data for b in view.buttons}
        for action in ("retry", "cancel", "duplicate", "rerun", "download"):
            self.assertIn(f"ux:{action}:abc", callbacks)

    def test_output_and_schedule_browsers(self):
        browsers = TelegramBrowsers()
        output = browsers.outputs("t", ["one"])
        schedule = browsers.schedules(["daily"])
        self.assertIn("Outputs", output.breadcrumbs)
        self.assertEqual(schedule.title, "Schedules")

    def test_destructive_action_requires_confirmation_view(self):
        view = UXRenderer().confirmation(
            title="Cancel task", body="Confirm cancellation?",
            action=UXAction.CANCEL, entity_id="t")
        callbacks = [b.callback_data for b in view.buttons]
        self.assertEqual(callbacks, ["ux:confirm:cancel:t", "ux:back:t"])

    def test_empty_browser_is_stable(self):
        view = TelegramBrowsers().tasks([])
        self.assertEqual(view.body, "—")
        self.assertEqual((view.page, view.pages), (0, 1))
