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


class DecorationIsNotCollapseTests(unittest.TestCase):
    """A correct answer must never be cut for containing a rule or a separator.

    Found live: a report ending in the 70 hyphens that ``unittest`` prints
    between its results was declared collapsed, truncated mid-answer and told
    the owner the model had broken down. The answer was flawless.
    """

    UNITTEST_REPORT = (
        "الخطوة 2 — مخرجات الاختبار:\n"
        "```\n"
        "test_replaces_spaces_with_hyphen (tests.test_slugify.SlugifyTest.test_replaces_spaces_with_hyphen) ... ok\n"
        "test_rejects_non_string (tests.test_slugify.SlugifyTest.test_rejects_non_string) ... ok\n"
        "\n"
        + "-" * 70 + "\n"
        "Ran 4 tests in 0.000s\n\n"
        "OK\n"
        "```\n\n"
        "الخطوة 3 — النشر تم عبر git init ثم gh repo create.\n"
    )

    def test_a_unittest_separator_is_not_a_collapse(self) -> None:
        self.assertIsNone(collapse_start(self.UNITTEST_REPORT))
        kept, truncated = truncated_answer(self.UNITTEST_REPORT)
        self.assertFalse(truncated)
        self.assertEqual(kept, self.UNITTEST_REPORT)

    def test_a_markdown_rule_is_not_a_collapse(self) -> None:
        report = "# العنوان\n\nنص.\n\n" + "-" * 200 + "\n\n## قسم\n\nنص آخر.\n"
        self.assertIsNone(collapse_start(report))

    def test_a_table_border_is_not_a_collapse(self) -> None:
        table = "| a | b |\n|---|---|\n" + ("| 1 | 2 |\n" * 12)
        self.assertIsNone(collapse_start(table))

    def test_a_real_collapse_next_to_a_separator_is_still_found(self) -> None:
        text = "نص سليم.\n\n" + "-" * 70 + "\n\n" + "ww" * 400
        self.assertIsNotNone(collapse_start(text))

    def test_masking_preserves_offsets(self) -> None:
        from bridge.telegram.rendering.collapse_guard import _mask_decoration

        text = "أول\n" + "-" * 10 + "\nثاني"
        masked = _mask_decoration(text)
        self.assertEqual(len(masked), len(text))
        self.assertEqual(masked.index("ثاني"), text.index("ثاني"))


class InlinePhraseLoopTests(unittest.TestCase):
    """The commonest collapse shape: the same few words repeated along one line.

    ``\\S*`` cannot span a space, so a phrase of two or three words repeated
    inline matched no rule at all, and a whole-line pattern cannot see it because
    the repetition never reaches a newline.
    """

    def test_a_multi_word_phrase_repeated_inline_is_found(self) -> None:
        text = "مقدمة صحيحة.\n\nتخطيط مساحة العمل " * 30
        self.assertIsNotNone(collapse_start(text))

    def test_an_english_phrase_repeated_inline_is_found(self) -> None:
        text = "Intro.\n\nthe deployment pipeline validates every release " * 20
        self.assertIsNotNone(collapse_start(text))

    def test_a_long_normal_sentence_is_not_a_collapse(self) -> None:
        text = (
            "نشرنا اليوم الإصدار الذي يضيف صفحة لكل خبر على حدة، مع زر يفتح "
            "المصدر الأصلي، ومسار كامل للنص إذا تعذّر العرض على شاشة واحدة."
        )
        self.assertIsNone(collapse_start(text))

    def test_a_list_that_repeats_a_short_word_is_not_a_collapse(self) -> None:
        text = "\n".join(f"- الخطوة {index}: الإعداد جاهز." for index in range(12))
        self.assertIsNone(collapse_start(text))

    def test_a_short_repetition_stays_allowed(self) -> None:
        text = "نص.\n\nنعم نعم نعم نعم نعم\n"
        self.assertIsNone(collapse_start(text))

    def test_the_guard_stays_fast_on_a_large_clean_answer(self) -> None:
        import time

        body = "\n\n".join(
            f"## فقرة رقم {index}\n\nنص عربي طويل بما فيه الكفاية ليمر دون ملاحظة، "
            "مع رقم وكلمة إنجليزية مثل pipeline و token و checksum."
            for index in range(120)
        )
        started = time.monotonic()
        self.assertIsNone(collapse_start(body))
        self.assertLess(time.monotonic() - started, 3.0)


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
