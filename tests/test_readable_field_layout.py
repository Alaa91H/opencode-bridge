"""A digest item must arrive as a channel post: labelled fields, room to breathe.

The answers that read worst are the ones an agent wrote as labelled fields glued
into one paragraph, with `[3]` citation markers no page can resolve and a
divider dangling at the end. These tests pin the repairs: one bullet per field,
one or two words before a colon before it is a field name, long prose broken at
sentence ends, markers dropped only when the answer really carries sources, and
a page that never ends on a rule.
"""

from __future__ import annotations

import unittest

from bridge.telegram.rendering.markdown_html import (
    build_pages,
    first_page,
    render_markdown_html,
)

RUN_ON = (
    "**التاريخ:** 15 أكتوبر 2026 · **التصنيف:** مؤكد · "
    "**الثقة: عالية** (فتحت صفحة الدعم [1] وإعلان OpenAI [2])"
)

ITEM = (
    "### 1. OpenAI — Rosalind Academic Plan\n"
    f"{RUN_ON}\n\n"
    "**المزود:** OpenAI. **ما يشمله:** حتى 30 مقعدًا مجانًا مع الوصول إلى نماذج OpenAI."
)


class FieldLayoutTests(unittest.TestCase):
    def test_each_labelled_field_becomes_its_own_bullet(self) -> None:
        html = first_page(ITEM).html
        self.assertIn("• <b>التاريخ:</b> 15 أكتوبر 2026", html)
        self.assertIn("• <b>التصنيف:</b> مؤكد", html)
        self.assertNotIn("<b>التاريخ:</b> 15 أكتوبر 2026 ·", html)

    def test_a_lonely_field_joins_the_list_instead_of_a_slab(self) -> None:
        text = "**ما حدث:** announcing the model.\n\n**التفاصيل:** it is fast.\n\nsummary tail"
        html = first_page(text).html
        self.assertIn("• <b>ما حدث:</b> announcing the model.", html)
        self.assertIn("• <b>التفاصيل:</b> it is fast.", html)
        self.assertIn("summary tail", html)

    def test_a_headline_that_swallowed_its_fields_keeps_only_the_headline(self) -> None:
        text = "### 7. Grok Voice 3.0 تتصدر مؤشر الكلام**التاريخ:** 14 أكتوبر · **التصنيف:** مؤكد"
        html = first_page(text).html
        self.assertIn("<b>▸ 7. Grok Voice 3.0 تتصدر مؤشر الكلام</b>", html)
        self.assertNotIn("الكلام<b>", html)
        self.assertIn("• <b>التاريخ:</b> 14 أكتوبر", html)

    def test_a_long_prose_paragraph_is_broken_into_readable_paragraphs(self) -> None:
        text = ". ".join(f"هذه جملة رقم {index} في الفقرة الطويلة" for index in range(1, 14))
        html = render_markdown_html(text)
        self.assertGreater(html.count("\n\n"), 0)

    def test_a_short_paragraph_is_never_split(self) -> None:
        self.assertEqual(render_markdown_html("جملة قصيرة واحدة."), "جملة قصيرة واحدة.")

    def test_a_url_is_never_cut_by_the_prose_split(self) -> None:
        url = "https://example.com/a/very/long/path/that/keeps/going/segment.md"
        text = f"الرابط هنا {url} ويجب أن يبقى كاملًا في مكانه. جملة أخرى. وجملة ثالثة."
        self.assertIn(url, render_markdown_html(text))


class FieldNameTests(unittest.TestCase):
    def test_a_short_label_is_bold(self) -> None:
        html = first_page("- ما حدث: إعلان عن نموذج جديد.").html
        self.assertIn("<b>ما حدث:</b>", html)

    def test_a_headline_with_a_colon_is_not_mistaken_for_a_field(self) -> None:
        html = first_page("7. Mistral تطلق Timestral: نماذج تنبؤ زمني من 5M إلى 209M.").html
        self.assertNotIn("<b>Mistral", html)

    def test_a_sentence_of_three_words_before_a_colon_stays_prose(self) -> None:
        html = first_page("- OpenAI تفتح Rosalind Academic: ثلاثون مقعدًا مجانًا.").html
        self.assertNotIn("<b>OpenAI", html)


class CitationTests(unittest.TestCase):
    def test_markers_disappear_when_the_answer_carries_its_sources(self) -> None:
        text = "الخبر الأول [1]\n\n[المصدر](https://example.com/a) هو المرجع."
        html = first_page(text).html
        self.assertNotIn("[1]", html)

    def test_markers_survive_when_there_is_no_source_to_point_at(self) -> None:
        html = first_page("انظر [1] و[2] للتفصيل.").html
        self.assertIn("[1]", html)

    def test_every_page_of_one_answer_agrees_about_markers(self) -> None:
        text = (
            "## أول\n\nخبر [1] مع [MSRC](https://example.com/a)\n\n"
            "## ثاني\n\nخبر [2] مع [مدار](https://example.com/b)"
        )
        pages = build_pages(text)
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertNotIn("]", page.html.replace("\n", ""))


class DividerTests(unittest.TestCase):
    def test_a_page_never_ends_on_a_divider(self) -> None:
        pages = build_pages("## عنوان\n\nنص قصير\n\n---\n\n## عنوان آخر\n\nنص آخر")
        for page in pages:
            self.assertFalse(page.html.rstrip().endswith("────────────"))

    def test_a_page_never_opens_on_a_divider(self) -> None:
        pages = build_pages("## عنوان\n\nنص قصير\n\n---\n\n## عنوان آخر\n\nنص آخر")
        for page in pages:
            self.assertFalse(page.html.startswith("────────────"))

    def test_a_rule_inside_a_page_stays(self) -> None:
        html = first_page("## أول\n\nنص قبل\n\n---\n\nنص بعد").html
        self.assertIn("────────────", html)


class TypographyTests(unittest.TestCase):
    def test_a_colon_glued_to_a_quote_gets_its_space(self) -> None:
        html = first_page("1. OpenAI تفتح «Rosalind Plan»:30 مقعدًا.").html
        self.assertIn("«Rosalind Plan»: 30", html)

    def test_a_time_is_left_alone(self) -> None:
        self.assertIn("12:30", first_page("الاجتماع عند 12:30 مساءً.").html)


if __name__ == "__main__":
    unittest.main()
