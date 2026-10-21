"""Unit tests for schedhistory argument grammar and prompt-file listing."""

from __future__ import annotations

import unittest

from bridge.telegram.commands.manage import ManageCommands
from bridge.telegram.commands.schedules import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    parse_history_args,
)


class ParseHistoryArgsTests(unittest.TestCase):
    def test_plain_name_keeps_default_limit(self) -> None:
        self.assertEqual(parse_history_args("news"), ("news", DEFAULT_HISTORY_LIMIT))

    def test_pipe_limit_is_parsed_and_clamped(self) -> None:
        self.assertEqual(parse_history_args("news | 5"), ("news", 5))
        self.assertEqual(parse_history_args("news|9999")[1], MAX_HISTORY_LIMIT)
        self.assertEqual(parse_history_args("news | 0")[1], 1)

    def test_non_numeric_tail_resolves_verbatim(self) -> None:
        name, count = parse_history_args("my | odd | name")
        self.assertEqual(name, "my | odd | name")
        self.assertEqual(count, DEFAULT_HISTORY_LIMIT)

    def test_empty_input_reports_empty_name(self) -> None:
        self.assertEqual(parse_history_args("   "), ("", DEFAULT_HISTORY_LIMIT))


class DescribePromptFilesTests(unittest.TestCase):
    def test_only_prompt_files_are_listed(self) -> None:
        records = [
            {"filename": "brief.md", "mime": "text/markdown"},
            {"filename": "photo.jpg", "mime": "image/jpeg"},
            {"filename": "data.json", "mime": "application/json"},
        ]
        self.assertEqual(
            ManageCommands.describe_prompt_files(records),
            ["brief.md", "data.json"],
        )

    def test_missing_names_are_ignored_safely(self) -> None:
        self.assertEqual(ManageCommands.describe_prompt_files([{}]), [])


if __name__ == "__main__":
    unittest.main()
