from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from prompt_enhancer import ResearchDepth, enhance_prompt


class PromptEnhancerTests(unittest.TestCase):
    def test_arabic_request_requires_shami_response(self) -> None:
        result = enhance_prompt("شو وضع المشروع؟")
        self.assertTrue(result.is_arabic)
        self.assertFalse(result.is_research)
        self.assertEqual(result.research_depth, ResearchDepth.NONE)
        self.assertIn("اللهجة الشامية", result.text)
        self.assertIn("شو وضع المشروع؟", result.text)

    def test_technical_news_becomes_deep_research(self) -> None:
        result = enhance_prompt("بدي آخر أخبار الذكاء الاصطناعي")
        self.assertTrue(result.is_arabic)
        self.assertTrue(result.is_research)
        self.assertEqual(result.research_depth, ResearchDepth.DEEP)
        self.assertIn("بحث عميق", result.text)
        self.assertIn("أعطِ الأولوية للمصادر", result.text)
        self.assertIn("تاريخ الحدث", result.text)
        self.assertIn("لم أتمكن من التحقق", result.text)

    def test_explicit_comprehensive_research_activates_extreme_protocol(self) -> None:
        result = enhance_prompt("أريد بحث عميق وشامل عن تنظيم الذكاء الاصطناعي في أوروبا")
        self.assertTrue(result.is_research)
        self.assertEqual(result.research_depth, ResearchDepth.EXTREME)
        self.assertIn("EXTREME DEEP RESEARCH", result.text)
        self.assertIn("بحث مضاد", result.text)
        self.assertIn("قسم «المصادر»", result.text)
        self.assertIn("مستوى الثقة", result.text)

    def test_local_file_lookup_does_not_become_web_research(self) -> None:
        result = enhance_prompt("ابحث عن الخطأ في ملف bot.py داخل المشروع")
        self.assertFalse(result.is_research)
        self.assertEqual(result.research_depth, ResearchDepth.NONE)
        self.assertNotIn("منهج الأدلة", result.text)

    def test_english_request_does_not_force_arabic(self) -> None:
        result = enhance_prompt("Give me the latest security news")
        self.assertFalse(result.is_arabic)
        self.assertTrue(result.is_research)
        self.assertEqual(result.research_depth, ResearchDepth.DEEP)
        self.assertIn("طابق لغة المستخدم", result.text)


if __name__ == "__main__":
    unittest.main()
