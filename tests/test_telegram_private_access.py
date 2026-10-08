"""Regression tests for private-only Telegram control-plane access."""

import unittest
from types import SimpleNamespace

from telegram.constants import ChatType

from bridge.telegram.middleware import TelegramAccessController


class TelegramPrivateAccessTests(unittest.TestCase):
    def setUp(self):
        self.controller = TelegramAccessController(
            allowed_users={123},
            allowed_chat_ids={-999, 456},
            reply=lambda *_: None,
            unauthorized_text=lambda: "denied",
            audit_write=lambda *_a, **_kw: None,
        )

    @staticmethod
    def update(user_id=123, chat_id=123, chat_type=ChatType.PRIVATE, is_bot=False):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id, is_bot=is_bot),
            effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        )

    def test_authorized_private_chat(self):
        self.assertTrue(self.controller.is_allowed(self.update()))

    def test_allowlisted_group_is_not_admin_chat(self):
        self.assertFalse(self.controller.is_allowed(self.update(chat_id=-999, chat_type=ChatType.GROUP)))

    def test_allowlisted_other_private_chat_is_not_admin_chat(self):
        self.assertFalse(self.controller.is_allowed(self.update(chat_id=456)))

    def test_unauthorized_user(self):
        self.assertFalse(self.controller.is_allowed(self.update(user_id=456, chat_id=456)))

    def test_bot_identity(self):
        self.assertFalse(self.controller.is_allowed(self.update(is_bot=True)))

    def test_missing_identity_or_chat(self):
        self.assertFalse(self.controller.is_allowed(SimpleNamespace(effective_user=None, effective_chat=None)))
        self.assertFalse(self.controller.is_allowed(SimpleNamespace(effective_user=SimpleNamespace(id=123, is_bot=False), effective_chat=None)))
