from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.freebuff.chat_store import (
    ChatRef,
    answer_text,
    find_latest_chat,
    iter_chats,
    load_messages,
    normalize_messages,
)


def _write_chat(config: Path, chat_id: str, payload: dict, *, project: str = "p1") -> Path:
    chat = config / "projects" / project / "chats" / chat_id
    chat.mkdir(parents=True, exist_ok=True)
    (chat / "chat-messages.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return chat


class ChatStoreTests(unittest.TestCase):
    def test_finds_chat_and_extracts_last_turn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw)
            _write_chat(
                config,
                "c1",
                {
                    "messages": [
                        {"role": "user", "content": "old question"},
                        {"role": "assistant", "content": "old answer", "completed": True},
                        {"role": "user", "content": "new question"},
                        {
                            "role": "assistant",
                            "parts": [
                                {"type": "reasoning", "text": "hidden"},
                                {"type": "text", "text": "new answer"},
                                {"type": "tool", "text": "raw tool output"},
                            ],
                            "finishTime": 1,
                        },
                    ]
                },
            )
            ref = find_latest_chat(config, since=0)
            self.assertIsNotNone(ref)
            assert ref is not None
            self.assertEqual(ref.chat_id, "c1")
            messages = load_messages(ref)
            self.assertEqual(answer_text(messages), "new answer")

    def test_log_jsonl_is_ignored_even_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw)
            chat = _write_chat(config, "c2", {"messages": [{"role": "assistant", "content": "ok"}]})
            (chat / "log.jsonl").write_text(
                '{"apiKey":"cb_should_never_be_read"}', encoding="utf-8"
            )
            refs = iter_chats(config)
            self.assertEqual([item.chat_id for item in refs], ["c2"])
            self.assertNotIn("cb_should_never_be_read", answer_text(load_messages(refs[0])))

    def test_invalid_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            chat = Path(raw) / "bad"
            chat.mkdir()
            (chat / "chat-messages.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                normalize_messages("{not json")
            with self.assertRaises(ValueError):
                load_messages(ChatRef(chat, "bad", "p", 0.0))

    def test_missing_store_returns_no_chat(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertIsNone(find_latest_chat(Path(raw), since=0))
            self.assertEqual(iter_chats(Path(raw)), [])


if __name__ == "__main__":
    unittest.main()
