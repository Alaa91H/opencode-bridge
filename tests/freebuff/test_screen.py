from __future__ import annotations

import unittest

from bridge.infrastructure.freebuff.screen import (
    ScreenAnalyzer,
    ScreenBuffer,
    clean_ansi,
    detect_busy,
    detect_error,
    detect_login,
    detect_prompt,
)


class ScreenBufferTests(unittest.TestCase):
    def test_strips_csi_and_osc_sequences(self) -> None:
        buffer = ScreenBuffer()
        buffer.feed("\x1b]0;title\x07\x1b[31mhello\x1b[0m\r\nworld")
        self.assertEqual(buffer.text, "hello\nworld")

    def test_carriage_return_redraws_the_current_line(self) -> None:
        buffer = ScreenBuffer()
        buffer.feed("working 10%\rworking 90%")
        self.assertEqual(buffer.text, "working 90%")

    def test_backspace_and_crlf(self) -> None:
        buffer = ScreenBuffer()
        buffer.feed("abc\b\bZ\r\nnext")
        self.assertEqual(buffer.text, "aZ\nnext")

    def test_clean_ansi_keeps_arabic(self) -> None:
        self.assertEqual(clean_ansi("\x1b[32mمرحبا\x1b[0m"), "مرحبا")


class ScreenDetectionTests(unittest.TestCase):
    def test_login_marker(self) -> None:
        self.assertTrue(detect_login("Please log in to Freebuff to continue"))
        self.assertFalse(detect_login("Freebuff is ready"))

    def test_busy_marker(self) -> None:
        self.assertTrue(detect_busy("Working… (esc to interrupt)"))
        self.assertFalse(detect_busy("❯ ask anything"))

    def test_prompt_marker(self) -> None:
        self.assertTrue(detect_prompt("❯"))
        self.assertFalse(detect_prompt("freebuff version 0.0.163"))

    def test_error_classification(self) -> None:
        kind, message = detect_error("429 Too Many Requests")  # type: ignore[misc]
        self.assertEqual(kind, "rate_limited")
        self.assertIn("حد الطلبات", message)
        self.assertIsNone(detect_error("everything is fine"))

    def test_idle_window_uses_last_output(self) -> None:
        analyzer = ScreenAnalyzer()
        analyzer.feed("hello", now=10.0)
        self.assertFalse(analyzer.idle_for(2.0, now=11.0))
        self.assertTrue(analyzer.idle_for(2.0, now=12.5))


if __name__ == "__main__":
    unittest.main()
