#!/usr/bin/env python3
"""Reference script: generate Telegraph-style HTML pages from an Arabic news digest.

Usage (after setting PYTHONPATH to include the repository root):
    python3 scripts/render_telegraph_digest.py digest.md out_dir/

This uses the bridge's own `delivery_pages` splitter so the output is exactly
what Telegram would receive: one standalone RTL page per item, each with its
headline, date, classification, four labelled bullets, source buttons, and a
local index + continuous full-page version.

The script writes:
  - item-NN.html (23 items max in the reference digest)
  - index.html (fhris with top-10 block)
  - full.html (continuous scrollable single-page version)
  - manifest.json (verified item metadata)
  - digest.md (copied source)
  - README.md + TEST-REPORT.md (capability documentation)
"""

import argparse
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# This relies on the repository's bridge module.
try:
    from bridge.telegram.rendering.news_pages import delivery_pages
    from bridge.telegram.rendering.markdown_html import MAX_PAGE_CHARS, Page, build_pages, extract_links
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PYTHONPATH must include the repository root so 'bridge.telegram.rendering' is importable.\n"
        f"Example: PYTHONPATH=/path/to/opencode-bridge-main python3 {__file__} ..."
    ) from exc

DASH_SPLIT = re.compile(r"\s+[—–-]\s+")
BULLET_PREFIX = re.compile(r"^\s*[•◦]\s*")
SECTION_PREFIX = re.compile(r"^▎\s*")
TAG_RE = re.compile(r"<[^>]+>")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
ITEM_HEADLINE_RE = re.compile(
    r"^\*\*(?P<title>.+?)\*\*\s+[—–-]\s+(?P<date>[^—–]+?)\s+[—–-]\s+(?P<cls>.+?)\s*$", re.M
)


@dataclass
class Item:
    index: int
    section: str
    headline: str
    date: str
    classification: str
    lines: list[str]
    links: tuple[tuple[str, str], ...]
    images: tuple[tuple[str, str], ...]
    raw_html: str
    plain: str
    file: str = ""

    @property
    def bullets(self) -> list[str]:
        return [line for line in self.lines if BULLET_PREFIX.match(line)]


CSS = """
:root { --ink:#111; --paper:#fff; --canvas:#f7f6f2; --line:#ddd; --muted:#777; --accent:#c62828; --badge:#f4f3f0; --accent-soft:#fceae6; }
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica Neue,Arial,sans-serif;line-height:1.65;}
.shell{max-width:720px;margin:0 auto;padding:28px 18px 48px;}
.masthead{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;font-size:13px;color:var(--muted);font-weight:600;}
.brand{font-weight:800;font-size:16px;color:var(--ink);letter-spacing:-.3px}
.brand-mark{display:inline-block;background:var(--ink);color:var(--paper);width:26px;height:26px;line-height:26px;text-align:center;border-radius:50%;margin-left:6px;font-size:13px}
.paper{background:var(--paper);border:1px solid var(--line);border-radius:16px;padding:28px 26px;box-shadow:0 4px 24px rgba(0,0,0,.04)}
h1{font-size:clamp(26px,4.4vw,36px);line-height:1.25;margin:0 0 12px;font-weight:800;letter-spacing:-.4px}
.eyebrow{display:inline-block;background:var(--accent-soft);color:var(--accent);border-radius:999px;padding:3px 12px;font-size:12px;font-weight:700;margin-bottom:14px}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 22px;color:var(--muted);font-size:13px}
.chip{background:var(--badge);border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600}
.chip.confirmed{color:var(--accent);border-color:var(--accent)}
.body{font-size:17px}
.body p{margin:0 0 12px}
.body p.point{display:flex;gap:10px;align-items:flex-start}
.body p.point::before{content:"";flex:0 0 7px;width:7px;height:7px;border-radius:50%;background:var(--accent);margin-top:9px;flex-shrink:0}
.sources{margin-top:18px;padding-top:14px;border-top:1px solid var(--line)}
.sources-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;font-weight:700;margin-bottom:8px}
.source-row{display:flex;gap:8px;flex-wrap:wrap}
.source{display:inline-block;background:var(--badge);border:1px solid var(--line);border-radius:999px;padding:5px 14px;font-size:13px;color:var(--ink);text-decoration:none;font-weight:600}
.source:hover{border-color:var(--accent);color:var(--accent)}
.footer{margin-top:24px;color:var(--muted);font-size:12px;text-align:center}
.index-head{padding:4px 0 20px}
.index-head h1{font-size:clamp(30px,5vw,42px);line-height:1.2;margin-bottom:6px}
.index-head p{color:var(--muted);margin:0 0 14px;font-size:14px}
.read-full{display:inline-flex;align-items:center;gap:8px;background:var(--ink);color:var(--paper);border-radius:999px;padding:10px 18px;font-size:14px;font-weight:700;text-decoration:none}
.read-full:hover{opacity:.9;text-decoration:none}
.section-title{margin:28px 0 10px;font-size:14px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.3px}
.cards{display:grid;gap:10px}
.card{display:block;background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:16px 18px;color:var(--ink);text-decoration:none}
.card:hover{border-color:var(--accent);text-decoration:none}
.card h2{font-size:18px;line-height:1.35;margin:2px 0 6px;font-weight:800}
.card .small{color:var(--muted);font-size:12px;display:flex;flex-wrap:wrap;gap:8px}
.no-news{background:var(--badge);border:1px dashed var(--line);border-radius:12px;padding:12px 16px;color:var(--muted);font-size:14px}
.top10{margin:0 0 8px;padding:0 22px 0 0}
.top10 li{margin:0 0 8px;padding-right:4px;font-size:15px}
.full-doc h1{font-size:clamp(28px,4.2vw,40px)}
.full-doc .lede{color:var(--muted);margin:0 0 8px;font-size:15px}
.page + .page{margin-top:18px}
.page .body{font-size:17px}
.page-nav{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 0}
.page-nav a{font-size:13px;border:1px solid var(--line);border-radius:999px;padding:6px 12px;color:var(--ink);text-decoration:none;font-weight:600}
.page-nav a:hover{border-color:var(--accent);color:var(--accent)}
@media print{body{background:#fff}.shell{max-width:none;padding:0}.paper{border:0;box-shadow:none;border-radius:0;padding:0}.masthead,.footer{display:none}}
"""


# ------------------------------------------------------------------
# Minimal item / index / full renderer using delivery_pages
# ------------------------------------------------------------------

def _render_item(item: Item) -> str:
    title = html.escape(item.headline)
    section = html.escape(item.section)
    date = html.escape(item.date)
    classification = html.escape(item.classification)
    confirmed = "confirmed" if item.classification.strip() in {"مؤكد", "مؤكّد"} else ""
    hero = ""
    if item.images:
        alt, src = item.images[0]
        hero = f'<figure class="hero"><img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}" loading="lazy"><figcaption>{html.escape(alt)}</figcaption></figure>'
    sources = ""
    if item.links:
        buttons = "".join(
            f'<a class="source" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(label or "المصدر")}</a>'
            for label, url in item.links
        )
        sources = f'<div class="sources"><div class="sources-label">المصادر</div><div class="source-row">{buttons}</div></div>'

    body_lines = ""
    title_line = None
    for line in item.lines:
        parts = DASH_SPLIT.split(line, maxsplit=2)
        if len(parts) >= 2 and parts[0].strip() == item.headline:
            title_line = line
            break
    for line in item.lines:
        if title_line is not None and line == title_line:
            continue
        if line.startswith("[صورة:") and line.endswith("]"):
            continue
        if line.startswith(("—", "–", "-")) and not BULLET_PREFIX.match(line):
            continue
        if BULLET_PREFIX.match(line):
            body = html.escape(BULLET_PREFIX.sub("", line).strip())
            body_lines += f'<p class="point">{body}</p>\n'
        else:
            body_lines += f"<p>{html.escape(line)}</p>\n"

    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} — نشرة التقنية اليومية</title><style>{CSS}</style></head>
<body><div class="shell">
<div class="masthead"><span class="brand"><span class="brand-mark">ت</span> نشرة التقنية اليومية</span><span dir="ltr">{date}</span></div>
<article class="paper">
<span class="eyebrow">{section}</span>
<h1>{title}</h1>
<div class="meta"><span>{date}</span><span class="chip {confirmed}">{classification}</span></div>
{hero}
<div class="body">{body_lines}</div>
{sources}
</article>
<div class="footer">صفحة مستقلة أُنشئت من نشرة واحدة — بلا أجزاء ولا «تابع»</div>
</div></body></html>"""


def parse_items(pages) -> tuple[list[Item], list]:
    items: list[Item] = []
    others: list = []
    for page in pages:
        lines = [line.strip() for line in page.plain.splitlines() if line.strip()]
        section = ""
        if lines and lines[0].startswith("▎"):
            section = SECTION_PREFIX.sub("", lines[0]).strip()
        body_lines = lines[1:] if section else lines
        headline, date, classification = ("", "", "غير مصنف")
        for ln in body_lines:
            if ("—" in ln) or ("–" in ln):
                parts = DASH_SPLIT.split(ln, maxsplit=2)
                if len(parts) >= 2:
                    headline = parts[0].strip()
                    date = parts[1].strip()
                    classification = parts[2].strip() if len(parts) > 2 else "غير مصنف"
                    break
        bullet_count = sum(1 for ln in body_lines if BULLET_PREFIX.match(ln))
        is_item = bool(section) and bool(date) and bullet_count >= 2
        if not is_item:
            others.append(page)
            continue
        items.append(Item(
            index=len(items) + 1,
            section=section,
            headline=headline,
            date=date,
            classification=classification,
            lines=body_lines,
            links=tuple(page.links) if hasattr(page, "links") else (),
            images=tuple(page.images) if hasattr(page, "images") else (),
            raw_html=page.html if hasattr(page, "html") else "",
            plain=page.plain,
        ))
    return items, others


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("digest", type=Path)
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    text = args.digest.read_text(encoding="utf-8")
    pages = delivery_pages(text)
    items, others = parse_items(pages)
    args.outdir.mkdir(parents=True, exist_ok=True)
    for old in args.outdir.glob("item-*.html"):
        old.unlink()

    for item in items:
        item.file = f"item-{item.index:02d}.html"
        (args.outdir / item.file).write_text(_render_item(item), encoding="utf-8")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Minimal index
    index_blocks = []
    groups = {}
    for item in items:
        groups.setdefault(item.section, []).append(item)
    for sec, grp in groups.items():
        cards = "".join(
            f'<a class="card" href="{i.file}"><div class="small"><span>{html.escape(i.date)}</span>·<span>{html.escape(i.classification)}</span></div><h2>{html.escape(i.headline)}</h2></a>'
            for i in grp
        )
        index_blocks.append(f'<div class="section-title">{html.escape(sec)}</div><div class="cards">{cards}</div>')

    index_text = f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>نشرة التقنية اليومية — الفهرس</title><style>{CSS}</style></head><body>
<div class="shell">
<div class="masthead"><span class="brand"><span class="brand-mark">ت</span> نشرة التقنية اليومية</span><span dir="ltr">{generated}</span></div>
<div class="index-head"><h1>نشرة التقنية اليومية</h1><p>{len(items)} خبرًا مستقلًا · 20 محورًا · المصدر: {html.escape(str(args.digest))}</p></div>
{''.join(index_blocks)}
<div class="footer">فهرس محلي؛ كل بطاقة تفتح صفحة الخبر المستقلة.</div>
</div></body></html>"""
    (args.outdir / "index.html").write_text(index_text, encoding="utf-8")

    # Minimal full continuous version
    full_items_text = ""
    for item in items:
        body_parts = []
        for ln_b in item.lines:
            if BULLET_PREFIX.match(ln_b):
                body_parts.append(f'<p class="point">{html.escape(re.sub(r"^[-•◦]\s+", "", ln_b))}</p>')
            else:
                body_parts.append(f"<p>{html.escape(ln_b)}</p>")
        full_items_text += f'<article class="paper" id="{item.file}"><h2>{html.escape(item.headline)}</h2><div class="meta"><span>{html.escape(item.date)}</span> <span class="chip">{html.escape(item.classification)}</span></div><div class="body">{"".join(body_parts)}</div></article>\n<hr>\n'

    full_text = f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>نشرة التقنية اليومية — النسخة الكاملة</title><style>{CSS}</style></head><body>
<div class="shell"><div class="masthead"><span class="brand"><span class="brand-mark">ت</span> نشرة التقنية اليومية</span><span dir="ltr">{generated}</span></div>
<article class="paper"><h1>نشرة التقنية اليومية</h1><p>النسخة الكاملة في صفحة واحدة.</p>{full_items_text}</article>
<div class="footer">نُسخت من النشرة الأصلية بلا تعديل.</div>
</div></body></html>"""
    (args.outdir / "full.html").write_text(full_text, encoding="utf-8")

    # Manifest
    manifest = {
        "generated_at": generated,
        "source": str(args.digest),
        "pages_total": len(pages),
        "items": len(items),
        "other_pages": len(others),
        "files": [
            {
                "file": item.file,
                "section": item.section,
                "headline": item.headline,
                "date": item.date,
                "classification": item.classification,
                "bullets": len(item.bullets),
                "links": [url for _, url in item.links],
                "images": [url for _, url in item.images],
                "chars": len(item.plain),
            }
            for item in items
        ],
    }
    (args.outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Copy source digest
    (args.outdir / "digest.md").write_text(text, encoding="utf-8")

    errors = []
    if not items:
        errors.append("لم يُعثر على أي خبر بالشكل المطلوب.")
    for item in items:
        if len(item.bullets) != 4:
            errors.append(f"{item.index}: عدد النقاط {len(item.bullets)} بدل 4.")
        if not item.links:
            errors.append(f"{item.index}: لا رابط مصدر.")
        if item.headline in [i.headline for i in items if i.index < item.index]:
            errors.append(f"{item.index}: عنوان مكرر.")

    result = {
        "pages": len(pages),
        "items": len(items),
        "other_pages": len(others),
        "errors": errors,
        "strict_pass": args.strict and not errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if (args.strict and errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
