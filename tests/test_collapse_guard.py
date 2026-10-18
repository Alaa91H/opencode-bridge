"""A collapsed answer must never be delivered as pages of repetition.

A model that degenerates does not fail loudly: it writes a few coherent
paragraphs and then loops — `wwwwww…` — to the end of its budget. Delivering
that is worse than delivering nothing, because the operator reads fifteen pages
of noise and blames the bridge. These tests pin the guard: the loop is found,
the cut lands on a coherent boundary, the tail note explains itself, nothing is
invented, and text that merely repeats a character stays untouched.
"""

from __future__ import annotations

import unittest

from bridge.telegram.rendering.collapse_guard import (
    TAIL_NOTE,
    collapse_start,
    is_degenerate,
    truncated_answer,
)
from bridge.telegram.rendering.news_pages import delivery_pages

CLEAN = (
    "# نشرة\n\n"
    "## عاجل\n\n"
    "**خبر أول** — 13 أكتوبر\n- ما حدث: شيء مهم.\n- تحليل: أثره واضح.\n\n"
    "## Propulsion\n\n"
    "**خبر ثانٍ** — 14 أكتوبر\n- ما حدث: شيء آخر.\n"
)

COLLAPSED = (
    "# نشرة\n\n"
    "## عاجل\n\n"
    "**خبر مفهوم** — 13 أكتوبر\n- ما حدث: تفاصيل مقروءة.\n\n"
    "لماذا يهم؟ هذا أول جيل من الحواسيب ww" + "w" * 900 + " انتهى."
)


class CollapseDetectionTests(unittest.TestCase):
    def test_a_clean_answer_is_not_degenerate(self) -> None:
        self.assertIsNone(collapse_start(CLEAN))
        self.assertFalse(is_degenerate(CLEAN))

    def test_a_stuck_character_run_is_found(self) -> None:
        self.assertTrue(is_degenerate(COLLAPSED))

    def test_a_repeated_phrase_is_found(self) -> None:
        text = "مقدمة قصيرة. " + "منتجات_html_توزيع " * 30 + " نهاية."
        self.assertTrue(is_degenerate(text))

    def test_a_repeated_line_is_found(self) -> None:
        text = "الفقرة الأولى.\n" + "تخطيط Shawn مساحة العمل.\n" * 8
        self.assertTrue(is_degenerate(text))

    def test_a_repeated_character_run_is_not_found(self) -> None:
        text = "الصيغة 100% مضمونة، والتقييم 100% نهائي، والنتيجة 100% واضحة."
        self.assertFalse(is_degenerate(text))

    def test_empty_text_is_clean(self) -> None:
        self.assertIsNone(collapse_start(""))
        self.assertFalse(is_degenerate(""))


class TruncationTests(unittest.TestCase):
    def test_a_collapsed_answer_keeps_the_readable_part(self) -> None:
        kept, truncated = truncated_answer(COLLAPSED)
        self.assertTrue(truncated)
        self.assertIn("خبر مفهوم", kept)
        self.assertIn(TAIL_NOTE, kept)

    def test_the_cut_lands_on_a_coherent_boundary(self) -> None:
        kept, _truncated = truncated_answer(COLLAPSED)
        body = kept.replace(TAIL_NOTE, "").rstrip()
        self.assertTrue(body.endswith((".", "!", "؟", "\n")))
        self.assertNotIn("wwwww", body)

    def test_a_clean_answer_is_returned_untouched(self) -> None:
        kept, truncated = truncated_answer(CLEAN)
        self.assertFalse(truncated)
        self.assertEqual(kept, CLEAN)

    def test_a_collapsed_answer_never_ships_the_loop(self) -> None:
        pages = delivery_pages(COLLAPSED)
        for page in pages:
            self.assertNotIn("wwwwwwwww", page.html)
        self.assertIn("توقف الرد هنا", pages[-1].html)

    def test_a_collapsed_answer_keeps_its_pages(self) -> None:
        pages = delivery_pages(COLLAPSED)
        self.assertGreater(len(pages), 1)
        self.assertTrue(any("خبر مفهوم" in page.html for page in pages))

    def test_a_clean_digest_is_unaffected(self) -> None:
        pages = delivery_pages(CLEAN)
        for page in pages:
            self.assertNotIn("توقف الرد هنا", page.html)


if __name__ == "__main__":
    unittest.main()
