import unittest
from unittest.mock import AsyncMock, patch

from bridge.domain.tasks.progress import ProgressEvent, ProgressRenderer, ProgressStage
from bridge.services.progress_service import ProgressService


class ProgressProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_all_required_stages_exist(self):
        self.assertEqual({x.value for x in ProgressStage}, {
            "queued", "downloading", "analyzing", "planning", "executing",
            "waiting_model", "processing_media", "uploading", "completed",
        })

    def test_renderer_is_separate_and_validates_percent(self):
        event = ProgressEvent("t", ProgressStage.EXECUTING, "step", 42)
        self.assertIn("42%", ProgressRenderer().render(event))
        with self.assertRaises(ValueError):
            ProgressEvent("t", ProgressStage.EXECUTING, percent=101)

    async def test_event_is_persisted_even_when_debounced(self):
        persist = AsyncMock()
        edit = AsyncMock()
        service = ProgressService(persist=persist, edit_message=edit, min_interval=10)
        service.bind_message("t", "message-1")
        with patch("bridge.services.progress_service.monotonic", side_effect=[100, 101]):
            first = await service.publish(ProgressEvent("t", ProgressStage.QUEUED))
            second = await service.publish(ProgressEvent("t", ProgressStage.ANALYZING))
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(persist.await_count, 2)
        self.assertEqual(edit.await_count, 1)

    async def test_one_message_principle_and_restart_rebind(self):
        persist = AsyncMock()
        edit = AsyncMock()
        service = ProgressService(persist=persist, edit_message=edit, min_interval=0)
        service.restore_message_binding("t", "same-message")
        await service.publish(ProgressEvent("t", ProgressStage.EXECUTING))
        await service.publish(ProgressEvent("t", ProgressStage.COMPLETED), force=True)
        self.assertEqual([call.args[0] for call in edit.await_args_list], ["same-message", "same-message"])

    async def test_unbound_event_is_still_persisted(self):
        persist = AsyncMock()
        edit = AsyncMock()
        service = ProgressService(persist=persist, edit_message=edit)
        sent = await service.publish(ProgressEvent("t", ProgressStage.DOWNLOADING))
        self.assertFalse(sent)
        persist.assert_awaited_once()
        edit.assert_not_awaited()
