"""Deterministic prompt enhancement for Telegram requests sent to OpenCode."""

from __future__ import annotations

import re
from dataclasses import dataclass

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
RESEARCH_RE = re.compile(
    r"(?:news|latest|recent|today|this week|research|report|compare|"
    r"news|اخبار|أخبار|اخر|آخر|حديث|جديد|مستجد|بحث|ابحث|تحليل|تقرير|قارن)",
    re.IGNORECASE,
)
TECH_RE = re.compile(
    r"(?:tech|technology|ai|artificial intelligence|software|security|cyber|"
    r"تقنية|تكنولوجيا|ذكاء اصطناعي|برمجة|أمن سيبراني|أمن معلومات)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EnhancedPrompt:
    text: str
    is_arabic: bool
    is_research: bool


def _language_instruction(is_arabic: bool) -> str:
    if is_arabic:
        return (
            "لغة الرد: استخدم اللهجة الشامية المحايدة والطبيعية، القريبة من الحكي اليومي ومن دون تصنّع. "
            "طابق مستوى عفوية المستخدم: خليك مختصر إذا كان مختصر، وفصّل بشكل مرتب إذا طلب شرح. "
            "استعمل عند اللزوم صيغ مثل رح، هلّق، فيك، بدك، هيك، بس، شو، ومش، من غير حشرها بكل جملة. "
            "لا تستخدم فولكلور أو مزاح مبالغ فيه أو لهجة مدينة محددة، ولا تستعمل يا زلمة أو لك إلا إذا بدأ المستخدم فيها وكان السياق مناسب. "
            "حافظ على أوامر البرمجة والمسارات وأسماء الخدمات كما هي، واستعمل فصحى خفيفة للمصطلحات التقنية لما بتكون أوضح. "
            "بالأخطاء: احكِ شو صار، ليش، وشو البديل الآمن بنبرة هادئة. لا تستخدم إيموجي تلقائيًا."
        )
    return "لغة الرد: طابق لغة المستخدم وتعليماته الخاصة باللغة إن وُجدت."


def _research_instruction(is_technical: bool) -> str:
    scope = "الأخبار والتطورات التقنية" if is_technical else "الموضوع المطلوب"
    return f"""
نوع المهمة: بحث حديث ومتعمق عن {scope}.

نفّذ المهمة بمنهج بحث احترافي، من دون سؤال المستخدم عن خطوات بديهية:
1. حدّد نطاق البحث والزمن المطلوب بدقة. إذا طلب المستخدم "الأحدث"، اعتمد أحدث معلومات متاحة وقت التنفيذ واذكر تاريخ القطع بوضوح.
2. ابحث على نطاق واسع في المصادر الأولية والرسمية أولاً، ثم المصادر الصحفية أو البحثية الموثوقة. استخدم أكثر من مصدر مستقل للحقائق المهمة أو المتنازع عليها.
3. ميّز بوضوح بين الإعلان، الإطلاق الفعلي، الرأي، والتسريب. لا تقدّم التوقعات كحقائق.
4. استخلص ما يهم المستخدم عملياً: ماذا حدث، لماذا يهم، الأثر، القيود، والخطوة التالية المقترحة إن كانت مناسبة.
5. قدّم نتيجة منظمة ومباشرة مع روابط المصادر وتواريخ النشر. لا تذكر ادعاءً زمنيًا أو رقميًا بلا مصدر موثوق.
6. لا تكتفِ بعناوين الأخبار؛ اقرأ المصدر الأصلي أو الصفحة الكاملة حين تكون متاحة.
""".strip()


def enhance_prompt(user_text: str) -> EnhancedPrompt:
    """Wrap a user request in an execution brief without changing its intent."""
    clean_text = user_text.strip()
    is_arabic = bool(ARABIC_RE.search(clean_text))
    is_research = bool(RESEARCH_RE.search(clean_text))
    is_technical = bool(TECH_RE.search(clean_text))

    sections = [
        "أنت عم تنفّذ طلب وصل من بوت تيليغرام مُصرّح.",
        _language_instruction(is_arabic),
        "حافظ على نية المستخدم الأصلية. نفّذ المطلوب مباشرة، واذكر النتيجة والتحقق وأي قيود حقيقية باختصار.",
    ]
    if is_research:
        sections.append(_research_instruction(is_technical))
    else:
        sections.append(
            "حسّن تنفيذ الطلب داخلياً: حدّد الهدف، القيود، ومعيار النجاح قبل العمل، "
            "بس لا تعيد صياغة الطلب للمستخدم ولا تطلب تأكيداً إلا إذا كان الغموض يمنع التنفيذ فعلاً."
        )
    sections.append(f"\n--- طلب المستخدم الأصلي ---\n{clean_text}\n--- نهاية الطلب ---")
    return EnhancedPrompt(text="\n\n".join(sections), is_arabic=is_arabic, is_research=is_research)
