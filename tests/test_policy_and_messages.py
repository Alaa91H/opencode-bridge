from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from block_patterns import check_build, check_hardline
from messages import build_blocked_message, user_error


class CommandPolicyTests(unittest.TestCase):
    def test_blocks_javascript_build(self) -> None:
        self.assertEqual(check_build("npm run build"), "بناء حزمة JavaScript")

    def test_blocks_rust_build(self) -> None:
        self.assertEqual(check_build("cargo build --release"), "بناء أو تثبيت Rust")

    def test_blocks_python_package_install(self) -> None:
        self.assertEqual(check_build("python3 -m pip install requests"), "تثبيت حزم Python على الخادم")

    def test_does_not_block_normal_arabic_text(self) -> None:
        self.assertIsNone(check_build("أنشئ خطة لتحسين المشروع دون تنفيذ أي أوامر."))

    def test_does_not_block_normal_english_text(self) -> None:
        self.assertIsNone(check_build("Please make a plan for the deployment."))

    def test_blocks_catastrophic_command(self) -> None:
        self.assertEqual(check_hardline("sudo reboot"), "إيقاف أو إعادة تشغيل النظام")


class ArabicErrorTests(unittest.TestCase):
    def test_network_error_is_translated(self) -> None:
        text = user_error(httpx.ConnectError("connection refused"))
        self.assertIn("تعذّر الاتصال", text)
        self.assertNotIn("connection refused", text)

    def test_build_message_explains_safe_alternative(self) -> None:
        text = build_blocked_message("بناء حزمة JavaScript")
        self.assertIn("خارج الخادم", text)


if __name__ == "__main__":
    unittest.main()
