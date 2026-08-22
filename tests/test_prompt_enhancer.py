from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from prompt_enhancer import enhance_prompt


class PromptEnhancerTests(unittest.TestCase):
    def test_arabic_request_requires_shami_response(self) -> None:
        result = enhance_prompt("شو وضع المشروع؟")
        self.assertTrue(result.is_arabic)
        self.assertIn("اللهجة الشامية", result.text)
        self.assertIn("شو وضع المشروع؟", result.text)

    def test_technical_news_becomes_deep_research(self) -> None:
        result = enhance_prompt("بدي آخر أخبار الذكاء الاصطناعي")
        self.assertTrue(result.is_arabic)
        self.assertTrue(result.is_research)
        self.assertIn("بحث حديث ومتعمق", result.text)
        self.assertIn("المصادر الأولية والرسمية", result.text)
        self.assertIn("تاريخ القطع", result.text)

    def test_english_request_does_not_force_arabic(self) -> None:
        result = enhance_prompt("Give me the latest security news")
        self.assertFalse(result.is_arabic)
        self.assertTrue(result.is_research)
        self.assertIn("طابق لغة المستخدم", result.text)


if __name__ == "__main__":
    unittest.main()
