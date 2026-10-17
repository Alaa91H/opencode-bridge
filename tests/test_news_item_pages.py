"""A digest must arrive the way global news channels deliver it: one item per message.

Each item keeps its own headline, labelled bullets, source link and photo, the
section it belongs to becomes its page label, and document order is preserved.
An answer that is not shaped like a digest must not be split into items.
"""

from __future__ import annotations

import unittest

from bridge.telegram.rendering.news_pages import delivery_pages

DIGEST = """# نشرة التقنية اليومية

**النطاق الزمني:** آخر 24 ساعة مع توسيع إلى 72 ساعة. **40 خبرًا بالضبط.**

## عاجل

**ثغرة Windows Shell تتجاوز Mark of the Web** — 13 أكتوبر 2026 — مؤكد
- ما حدث: Microsoft أصدرة CVE-2026-96951 تسمح بتجاوز حماية MotW عبر الشبكة.
- التفاصيل: CVSS 8.8 ومستغلة في البريّة. المصدر: [MSRC](https://msrc.microsoft.com/advisory)
- تحليل: تجاوز MotW يفتح الباب لمرفقات حميلة. ![لقطة](https://example.com/motw.png)

**ثغرة Fortinet تتجاوز الحماية** — 12 أكتوبر 2026 — مؤكد
- ما حدث: خلل في واجهة الإدارة يسمح بالتجاوز.
- تحليل: يحتاج تحديثاً عاجلاً. المصدر: [Fortinet](https://example.com/fortinet)

## الذكاء الاصطناعي

**Meta تفتح أوزان Muse Spark** — 15 أكتوبر 2026 — مؤكد
- ما حدث: Superintelligence Labs نشرت أوزان نموذج MoE بـ816 مليار معامل.
- تحليل: منافسة مفتوحة. المصدر: [Meta](https://research.meta.ai/blog)
"""


class NewsItemPageTests(unittest.TestCase):
    def test_every_item_becomes_its_own_page(self) -> None:
        pages = delivery_pages(DIGEST)
        items = [page for page in pages if "المصدر" in page.plain]
        self.assertEqual(len(items), 3)

    def test_item_page_carries_label_headline_and_bold_field_names(self) -> None:
        pages = delivery_pages(DIGEST)
        first_item = next(page for page in pages if "CVE-2026-96951" in page.plain)
        self.assertIn("<b>▎ عاجل</b>", first_item.html)
        self.assertIn("<b>ثغرة Windows Shell تتجاوز Mark of the Web</b>", first_item.html)
        self.assertIn("<b>ما حدث:</b>", first_item.html)
        self.assertIn("<b>تحليل:</b>", first_item.html)

    def test_each_item_keeps_only_its_own_source_link(self) -> None:
        pages = delivery_pages(DIGEST)
        by_item = {
            "CVE-2026-96951": "https://msrc.microsoft.com/advisory",
            "خلل في واجهة الإدارة": "https://example.com/fortinet",
            "Superintelligence Labs": "https://research.meta.ai/blog",
        }
        for marker, url in by_item.items():
            page = next(page for page in pages if marker in page.plain)
            self.assertEqual([link for _label, link in page.links], [url])

    def test_item_photo_travels_with_its_own_page(self) -> None:
        pages = delivery_pages(DIGEST)
        page = next(page for page in pages if "CVE-2026-96951" in page.plain)
        self.assertEqual(page.images, (("لقطة", "https://example.com/motw.png"),))
        self.assertEqual(pages[-1].images, ())

    def test_bold_prose_is_not_mistaken_for_an_item(self) -> None:
        pages = delivery_pages(DIGEST)
        opening = next(page for page in pages if "النطاق الزمني" in page.plain)
        # The opening line stays prose on its own page, never an item headline.
        self.assertNotIn("<b>▎ ", opening.html)
        self.assertIn("<b>النطاق الزمني:</b>", opening.html)

    def test_document_order_is_preserved(self) -> None:
        pages = delivery_pages(DIGEST)
        order = [
            "CVE-2026-96951",
            "خلل في واجهة الإدارة",
            "Superintelligence Labs",
        ]
        positions = [
            next(index for index, page in enumerate(pages) if marker in page.plain)
            for marker in order
        ]
        self.assertEqual(positions, sorted(positions))

    def test_a_plain_answer_is_not_split_into_items(self) -> None:
        answer = "الجواب: تم التنفيذ بنجاح.\n\n- الخطوة الأولى\n- الخطوة الثانية"
        pages = delivery_pages(answer)
        self.assertEqual(len(pages), 1)
        self.assertNotIn("<b>▎ ", pages[0].html)
        self.assertEqual(pages[0].images, ())


if __name__ == "__main__":
    unittest.main()
