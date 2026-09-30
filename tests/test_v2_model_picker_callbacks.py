import unittest
from unittest.mock import AsyncMock

from bridge.services.model_selection_service import ModelSelectionError
from bridge.telegram.callbacks.model_picker import ModelPickerCallbackAdapter
from bridge.telegram.rendering import model_picker as picker

# The level names and order OpenCode itself shows in its reasoning selector.
UI_LEVELS = ("max", "xhigh", "high", "medium", "low", "minimal")


class FakeMessage:
    def __init__(self):
        self.chat_id = 4242
        self.message_id = 77
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self


class FakeQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message if message is not None else FakeMessage()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class FakeBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, *, chat_id, message_id, text, reply_markup=None):
        self.edits.append((chat_id, message_id, text, reply_markup))
        return self


class FakeUpdate:
    def __init__(self, data=None, user_id=555, message=None):
        self.effective_user = type("U", (), {"id": user_id})()
        self.effective_message = message if message is not None else FakeMessage()
        self.callback_query = FakeQuery(data) if data is not None else None
        self.bot = FakeBot()

    def get_bot(self):
        return self.bot


def make_view(count=8, current="opencode/m0", variants=UI_LEVELS):
    return picker_model_view([f"opencode/m{index}" for index in range(count)], current, variants)


def picker_model_view(choices, current, variants, current_variant="max"):
    from bridge.services.model_selection_service import ModelChoice, ModelSelectionView

    return ModelSelectionView(
        current_model=current,
        current_variant=current_variant,
        preferred_model=None,
        pinned=False,
        choices=tuple(
            ModelChoice(model_id=model_id, label=model_id.split("/")[-1], variants=variants if index == 0 else ())
            for index, model_id in enumerate(choices)
        ),
    )


class ModelPickerCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.view = make_view()
        self.service = AsyncMock()
        self.service.view = AsyncMock(return_value=self.view)
        self.service.select_model = AsyncMock(return_value=self.view)
        self.service.select_variant = AsyncMock(return_value=self.view)
        self.service.auto = AsyncMock(return_value=self.view)
        self.adapter = ModelPickerCallbackAdapter(
            service=self.service,
            is_allowed=lambda update: True,
            answer=lambda query, text=None, show_alert=False: query.answer(text, show_alert=show_alert),
        )

    async def test_show_replies_with_keyboard(self):
        update = FakeUpdate(data=None)
        await self.adapter.show(update)
        self.assertEqual(len(update.effective_message.replies), 1)
        self.assertIn("reply_markup", update.effective_message.replies[0][1])

    async def test_next_page_edits_the_existing_message(self):
        update = FakeUpdate(data="mdl:p:1")
        await self.adapter.handle(update, None)
        self.assertEqual(len(update.bot.edits), 1)
        chat_id, message_id, _text, keyboard = update.bot.edits[0]
        self.assertEqual((chat_id, message_id), (4242, 77))
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("2/2", labels)
        self.assertIn("m7", labels)
        self.assertIn("‹ السابق", labels)
        self.assertNotIn("m0", labels)
        payloads = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("mdl:m:7", payloads)
        self.assertTrue(all(len(value.encode()) <= 64 for value in payloads))

    async def test_model_button_shows_variant_levels(self):
        update = FakeUpdate(data="mdl:m:0")
        await self.adapter.handle(update, None)
        self.service.select_model.assert_awaited_once_with("555", 0)
        self.assertIn("مستويات الاستدلال", update.bot.edits[0][2])

    async def test_rank_prefix_numbers_from_one_and_never_uses_zero(self):
        self.assertEqual(picker.rank_prefix(1), "1⃣")
        self.assertEqual(picker.rank_prefix(6), "6⃣")
        self.assertEqual(picker.rank_prefix(11), "11.")
        for position in range(1, 8):
            self.assertNotIn("0⃣", picker.rank_prefix(position))

    async def test_variant_levels_are_numbered_from_one(self):
        update = FakeUpdate(data="mdl:m:0")
        await self.adapter.handle(update, None)
        labels = [row[0].text for row in update.bot.edits[0][3].inline_keyboard]
        self.assertTrue(labels[1].startswith("1⃣"))
        self.assertTrue(labels[2].startswith("2⃣"))
        self.assertFalse(any(label.startswith("0⃣") for label in labels))

    async def test_variant_levels_are_one_per_row_strongest_first_bilingual(self):
        update = FakeUpdate(data="mdl:m:0")
        await self.adapter.handle(update, None)
        rows = update.bot.edits[0][3].inline_keyboard
        self.assertTrue(all(len(row) == 1 for row in rows))
        labels = [row[0].text for row in rows]
        self.assertEqual(labels[0], picker.AUTO_LEVEL_TEXT)
        expected = [
            "Max — أقصى استدلال",
            "Xhigh — استدلال عالٍ جدًا",
            "High — استدلال عالٍ",
            "Medium — استدلال متوسط",
            "Low — استدلال منخفض",
            "Minimal — أقل استدلال",
        ]
        for position, label in enumerate(expected, start=1):
            self.assertIn(label, labels[position])
        self.assertEqual(labels[-1], picker.BACK_TEXT)
        self.assertEqual(len(labels), len(expected) + 2)

    async def test_current_level_is_marked_and_others_are_not(self):
        self.view = picker_model_view(["opencode/m0"], "opencode/m0", ("xhigh", "high"), current_variant="high")
        self.service.view = AsyncMock(return_value=self.view)
        self.service.select_model = AsyncMock(return_value=self.view)
        adapter = ModelPickerCallbackAdapter(
            service=self.service,
            is_allowed=lambda update: True,
            answer=lambda query, text=None, show_alert=False: query.answer(text, show_alert=show_alert),
        )
        update = FakeUpdate(data="mdl:m:0")
        await adapter.handle(update, None)
        labels = [row[0].text for row in update.bot.edits[0][3].inline_keyboard]
        marked = [label for label in labels if "✅" in label]
        self.assertEqual(len(marked), 1)
        self.assertIn("High", marked[0])
        self.assertTrue(marked[0].startswith("2"))

    async def test_model_without_variants_only_offers_automatic(self):
        self.view = picker_model_view(["opencode/m0", "opencode/m1"], "opencode/m0", ())
        self.service.view = AsyncMock(return_value=self.view)
        self.service.select_model = AsyncMock(return_value=self.view)
        adapter = ModelPickerCallbackAdapter(
            service=self.service,
            is_allowed=lambda update: True,
            answer=lambda query, text=None, show_alert=False: query.answer(text, show_alert=show_alert),
        )
        update = FakeUpdate(data="mdl:m:0")
        await adapter.handle(update, None)
        labels = [row[0].text for row in update.bot.edits[0][3].inline_keyboard]
        self.assertEqual(labels, [picker.AUTO_LEVEL_TEXT, picker.BACK_TEXT])

    async def test_variant_button_confirms_and_returns_to_models(self):
        update = FakeUpdate(data="mdl:v:0:2")
        await self.adapter.handle(update, None)
        self.service.select_variant.assert_awaited_once_with("555", 0, 2)
        text = update.bot.edits[0][2]
        self.assertIn("تم اعتماد", text)

    async def test_auto_button_clears_the_pin(self):
        update = FakeUpdate(data="mdl:auto")
        await self.adapter.handle(update, None)
        self.service.auto.assert_awaited_once_with("555")
        self.assertIn("اختيار النموذج", update.bot.edits[0][2])

    async def test_noop_page_indicator_does_not_edit(self):
        update = FakeUpdate(data="mdl:noop")
        await self.adapter.handle(update, None)
        self.assertEqual(update.bot.edits, [])
        self.assertEqual(update.callback_query.answers, [(None, False)])

    async def test_back_returns_to_the_model_list(self):
        update = FakeUpdate(data="mdl:back")
        await self.adapter.handle(update, None)
        self.assertEqual(len(update.bot.edits), 1)
        self.assertIn("اختيار النموذج", update.bot.edits[0][2])

    async def test_stale_selection_answers_with_alert_and_refreshes(self):
        self.service.select_model = AsyncMock(side_effect=ModelSelectionError("gone"))
        update = FakeUpdate(data="mdl:m:0")
        await self.adapter.handle(update, None)
        self.assertEqual(update.callback_query.answers[0][0], "gone")
        self.assertTrue(update.callback_query.answers[0][1])
        self.assertEqual(len(update.bot.edits), 1)

    async def test_unauthorized_press_is_rejected_before_any_write(self):
        adapter = ModelPickerCallbackAdapter(
            service=self.service,
            is_allowed=lambda update: False,
            answer=lambda query, text=None, show_alert=False: query.answer(text, show_alert=show_alert),
        )
        update = FakeUpdate(data="mdl:m:0")
        await adapter.handle(update, None)
        self.service.select_model.assert_not_awaited()
        self.assertEqual(update.bot.edits, [])
        self.assertTrue(update.callback_query.answers[0][1])

    async def test_foreign_callback_prefix_is_ignored(self):
        update = FakeUpdate(data="sch:page:0")
        await self.adapter.handle(update, None)
        self.assertEqual(update.bot.edits, [])
        self.assertEqual(update.callback_query.answers, [])

    async def test_malformed_index_does_not_crash(self):
        update = FakeUpdate(data="mdl:m:not-a-number")
        await self.adapter.handle(update, None)
        self.assertEqual(len(update.callback_query.answers), 1)
