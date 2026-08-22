from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from prompt_enhancer import RequestIntent, ResearchDepth, ResearchMode, enhance_prompt


class PromptEnhancerTests(unittest.TestCase):
    def test_arabic_general_request_stays_in_auto_mode(self) -> None:
        result = enhance_prompt("شو وضع المشروع؟")
        self.assertTrue(result.is_arabic)
        self.assertFalse(result.is_research)
        self.assertEqual(result.intent, RequestIntent.GENERAL)
        self.assertEqual(result.research_depth, ResearchDepth.NONE)
        self.assertIn("اللهجة الشامية", result.text)
        self.assertIn("وضع التنفيذ: AUTO", result.text)

    def test_technical_news_becomes_deep_news_research(self) -> None:
        result = enhance_prompt("بدي آخر أخبار الذكاء الاصطناعي")
        self.assertTrue(result.is_arabic)
        self.assertTrue(result.is_research)
        self.assertEqual(result.intent, RequestIntent.NEWS)
        self.assertEqual(result.research_depth, ResearchDepth.DEEP)
        self.assertIn("بحث عميق", result.text)
        self.assertIn("تاريخ الحدث", result.text)
        self.assertIn("لم أتمكن من التحقق", result.text)

    def test_natural_comparison_selects_comparison_intent(self) -> None:
        result = enhance_prompt("قارن بين Claude وGemini للاستخدام البرمجي")
        self.assertTrue(result.is_research)
        self.assertEqual(result.intent, RequestIntent.COMPARISON)
        self.assertEqual(result.research_depth, ResearchDepth.DEEP)
        self.assertIn("للمقارنة", result.text)

    def test_explicit_extreme_mode_activates_extreme_protocol(self) -> None:
        result = enhance_prompt("تنظيم الذكاء الاصطناعي في أوروبا", requested_mode=ResearchMode.EXTREME)
        self.assertTrue(result.is_research)
        self.assertEqual(result.intent, RequestIntent.RESEARCH)
        self.assertEqual(result.requested_mode, ResearchMode.EXTREME)
        self.assertEqual(result.research_depth, ResearchDepth.EXTREME)
        self.assertIn("EXTREME DEEP RESEARCH", result.text)
        self.assertIn("بحث مضاد", result.text)
        self.assertIn("قسم «المصادر»", result.text)

    def test_explicit_fact_check_mode_overrides_missing_keywords(self) -> None:
        result = enhance_prompt("هذه العبارة", requested_mode=ResearchMode.FACT_CHECK)
        self.assertTrue(result.is_research)
        self.assertEqual(result.intent, RequestIntent.FACT_CHECK)
        self.assertEqual(result.research_depth, ResearchDepth.DEEP)
        self.assertIn("صحيح، مرجح، غير متحقق، مضلل، أو خطأ", result.text)

    def test_local_file_lookup_does_not_become_web_research(self) -> None:
        result = enhance_prompt("ابحث عن الخطأ في ملف bot.py داخل المشروع")
        self.assertFalse(result.is_research)
        self.assertEqual(result.intent, RequestIntent.LOCAL_LOOKUP)
        self.assertEqual(result.research_depth, ResearchDepth.NONE)
        self.assertNotIn("منهج الأدلة", result.text)

    def test_english_request_does_not_force_arabic(self) -> None:
        result = enhance_prompt("Give me the latest security news")
        self.assertFalse(result.is_arabic)
        self.assertTrue(result.is_research)
        self.assertEqual(result.intent, RequestIntent.NEWS)
        self.assertEqual(result.research_depth, ResearchDepth.DEEP)
        self.assertIn("طابق لغة المستخدم", result.text)


if __name__ == "__main__":
    unittest.main()
