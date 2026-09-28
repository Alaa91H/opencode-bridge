import unittest
from types import SimpleNamespace

from bridge.telegram.rendering.schedule_browser import delete_confirmation, schedule_actions, schedule_page


def job(i, enabled=True):
    return SimpleNamespace(id=i, name=f"job-{i}", enabled=enabled)


class ScheduleBrowserTests(unittest.TestCase):
    def test_pagination(self):
        text, markup = schedule_page([job(i) for i in range(14)], 1, 6)
        self.assertEqual(text, "إدارة الجدولة")
        data = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn("sch:page:0", data)
        self.assertIn("sch:page:2", data)
        self.assertIn("sch:show:6:1", data)
        self.assertNotIn("sch:show:0:1", data)

    def test_actions_include_required_management(self):
        markup = schedule_actions(job(7), 2)
        data = {b.callback_data for row in markup.inline_keyboard for b in row}
        self.assertTrue({"sch:pause:7:2","sch:run:7:2","sch:history:7:2",
                         "sch:duplicate:7:2","sch:edit:7:2","sch:delete:7:2"} <= data)
        resumed = schedule_actions(job(7, False), 2)
        self.assertIn("sch:resume:7:2", {b.callback_data for row in resumed.inline_keyboard for b in row})

    def test_delete_requires_confirmation(self):
        markup = delete_confirmation(9, 3)
        data = {b.callback_data for row in markup.inline_keyboard for b in row}
        self.assertEqual(data, {"sch:deleteyes:9:3", "sch:show:9:3"})


if __name__ == "__main__":
    unittest.main()
