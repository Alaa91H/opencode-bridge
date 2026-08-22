"""Deterministic prompt enhancement for Telegram requests sent to OpenCode.

Research requests receive an evidence-first brief that adapts its depth without
changing the user's intent or turning local code/file searches into web research.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
RESEARCH_RE = re.compile(
    r"(?:\b(?:search|research|investigate|verify|fact[ -]?check|latest|recent|current|"
    r"news|report|compare|comparison|price|pricing|release|update|sources?|references?)\b|"
    r"(?:ابحث|بحث|استقص|تحقق|تدقيق|مصادر|مراجع|أخبار|اخبار|آخر|اخر|أحدث|احدث|"
    r"حالي|اليوم|حديث|جديد|مستجد|تقرير|تحليل|قارن|مقارنة|سعر|أسعار|اصدار|إصدار|تحديث))",
    re.IGNORECASE,
)
DEEP_RESEARCH_RE = re.compile(
    r"(?:\b(?:deep research|in[ -]?depth|comprehensive|complete investigation|all about)\b|"
    r"(?:بحث\s+(?:عميق|شامل|كامل)|بالتفصيل|تحقيق\s+كامل|أريد\s+كل\s+شيء|كل\s+شيء))",
    re.IGNORECASE,
)
TEMPORAL_RE = re.compile(
    r"(?:\b(?:latest|recent|current|today|newest|this week|updated?)\b|"
    r"(?:آخر|اخر|أحدث|احدث|حالي|اليوم|هلّق|هلق|الآن|حديث|جديد|مستجد))",
    re.IGNORECASE,
)
COMPARISON_RE = re.compile(
    r"(?:\b(?:compare|comparison|versus|vs\.?|difference)\b|(?:قارن|مقارنة|فرق|مقابل))",
    re.IGNORECASE,
)
HIGH_STAKES_RE = re.compile(
    r"(?:\b(?:law|legal|regulation|policy|medical|health|finance|financial|investment|price|pricing)\b|"
    r"(?:قانون|قانوني|تنظيم|سياسة|طبي|صحة|مالي|استثمار|سعر|أسعار))",
    re.IGNORECASE,
)
TECH_RE = re.compile(
    r"(?:tech|technology|ai|artificial intelligence|software|security|cyber|"
    r"تقنية|تكنولوجيا|ذكاء اصطناعي|برمجة|أمن سيبراني|أمن معلومات)",
    re.IGNORECASE,
)
# A search inside the bot/project must remain an operational request, not be sent
# through a multi-source web-research workflow merely because it contains "search".
LOCAL_CONTEXT_RE = re.compile(
    r"(?:\b(?:in (?:the )?(?:project|repo|repository|code|file|files|logs?)|"
    r"find in|grep|source code)\b|(?:في\s+(?:المشروع|الكود|الملف|الملفات|السجل|السجلات)|"
    r"داخل\s+(?:المشروع|الكود|الملف|الملفات)|مستودعنا|السورس))",
    re.IGNORECASE,
)
EXPLICIT_WEB_RESEARCH_RE = re.compile(
    r"(?:\b(?:web|internet|online|sources?|references?|deep research)\b|"
    r"(?:الويب|الإنترنت|الانترنت|مصادر|مراجع|بحث\s+(?:عميق|شامل|كامل)))",
    re.IGNORECASE,
)


class ResearchDepth(StrEnum):
    """How much evidence-gathering structure to request from the agent."""

    NONE = "none"
    STANDARD = "standard"
    DEEP = "deep"
    EXTREME = "extreme"


@dataclass(frozen=True)
class EnhancedPrompt:
    text: str
    is_arabic: bool
    is_research: bool
    research_depth: ResearchDepth = ResearchDepth.NONE


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


def _is_research_request(clean_text: str) -> bool:
    """Identify external evidence-gathering, while excluding local project lookup."""
    if not RESEARCH_RE.search(clean_text):
        return False
    return not (LOCAL_CONTEXT_RE.search(clean_text) and not EXPLICIT_WEB_RESEARCH_RE.search(clean_text))


def _research_depth(clean_text: str) -> ResearchDepth:
    if DEEP_RESEARCH_RE.search(clean_text):
        return ResearchDepth.EXTREME
    if TEMPORAL_RE.search(clean_text) or COMPARISON_RE.search(clean_text) or HIGH_STAKES_RE.search(clean_text):
        return ResearchDepth.DEEP
    return ResearchDepth.STANDARD


def _research_instruction(depth: ResearchDepth, is_technical: bool) -> str:
    scope = "موضوعًا تقنيًا" if is_technical else "الموضوع المطلوب"
    level_name = {
        ResearchDepth.STANDARD: "بحث موثّق",
        ResearchDepth.DEEP: "بحث عميق",
        ResearchDepth.EXTREME: "EXTREME DEEP RESEARCH",
    }[depth]
    temporal_rule = (
        "بما أن الطلب زمني أو قابل للتغيّر: تحقّق من أحدث مصدر رسمي متاح وقت التنفيذ، "
        "واذكر تاريخ الحدث منفصلًا عن تاريخ النشر أو التحديث عند اختلافهما."
        if depth in {ResearchDepth.DEEP, ResearchDepth.EXTREME}
        else "إذا كانت أي معلومة قابلة للتغيّر، تحقّق من حداثتها قبل عرضها كحالية."
    )
    depth_rule = {
        ResearchDepth.STANDARD: (
            "استخدم بحثًا مركزًا، ولا تحوّل سؤالًا بسيطًا إلى تقرير طويل؛ لكن لا تقدّم ادعاءً مهمًا بلا دليل مباشر."
        ),
        ResearchDepth.DEEP: (
            "قسّم الموضوع داخليًا إلى محاور، ووسّع الاستعلامات بالمرادفات والأسماء الرسمية والبديلة واللغة الإنجليزية "
            "واللغة المحلية ذات الصلة. تحقّق متقاطعًا من الحقائق الجوهرية قبل الاستنتاج."
        ),
        ResearchDepth.EXTREME: (
            "نفّذ المراحل كاملة: استكشاف، تقسيم لمحاور، توسيع الاستعلامات، تعمّق مستقل بكل محور، تحقق متقاطع، "
            "بحث مضاد عن النفي والانتقادات والتفسيرات البديلة، ثم مراجعة أخيرة لكل ادعاء قبل الإخراج."
        ),
    }[depth]
    return f"""
نوع المهمة: {level_name} عن {scope}.

اتبّع منهج الأدلة التالي من دون تغيير نية المستخدم:
1. افهم السؤال الفعلي وحدد الكيانات والأسماء والإصدارات والتواريخ والمناطق والقيود، ثم حدّد داخليًا إن كان معلوماتيًا أو مقارنًا أو تحليليًا أو تقنيًا أو إخباريًا أو تنظيميًا أو متعلقًا بأسعار/مواصفات. لا تفترض معنى مصطلح غامض؛ اذكر الغموض فقط إذا كان مؤثرًا على النتيجة.
2. {depth_rule}
3. أعطِ الأولوية للمصادر الأولية والرسمية والوثائق والجهات الحكومية والأبحاث الأصلية، ثم للمصادر المستقلة الموثوقة. لا تعامل إعادة نشر المصدر نفسه كتحقق مستقل، ولا تجعل المنتديات أو المحتوى التسويقي دليلًا وحيدًا على ادعاء مهم.
4. لكل حقيقة محورية أو محل نزاع، استخدم مصدرًا أوليًا/رسميًا متى كان متاحًا، أو مصدرين مستقلين موثوقين على الأقل. افصل بوضوح بين الحقائق المؤكدة، والمعلومات المرجحة، والاستنتاج التحليلي، وغير المؤكد.
5. ابحث عمدًا عن أدلة مخالفة أو انتقادات أو قيود أو تغييرات أحدث. إذا تعارضت المصادر، اعرض التعارض وتواريخ المصادر وسبب ترجيح دليل على آخر؛ لا تدمج أرقامًا مختلفة من دون تفسير.
6. {temporal_rule}
7. ممنوع اختلاق مصدر أو رابط أو اقتباس أو رقم أو تاريخ. إذا لم يمكن التحقق من معلومة فقل بوضوح: «لم أتمكن من التحقق من هذه المعلومة من مصدر موثوق.»

صياغة النتيجة:
- ابدأ بالخلاصة المباشرة، ثم التفاصيل التي يحتاجها المستخدم فقط.
- اربط كل ادعاء مهم بإحالة رقمية متسقة مثل [1] تؤدي إلى المصدر الذي يثبته مباشرة.
- أضف قسم «المصادر» في النهاية يضم فقط المصادر المستخدمة فعليًا، مع اسم المصدر والرابط وتاريخ النشر أو التحديث عند توفره.
- عند وجود عدم يقين مهم، اذكر مستوى الثقة: عالية أو متوسطة أو منخفضة، وسبب ذلك باختصار.
""".strip()


def enhance_prompt(user_text: str) -> EnhancedPrompt:
    """Wrap a user request in an execution brief without changing its intent."""
    clean_text = user_text.strip()
    is_arabic = bool(ARABIC_RE.search(clean_text))
    is_research = _is_research_request(clean_text)
    is_technical = bool(TECH_RE.search(clean_text))
    depth = _research_depth(clean_text) if is_research else ResearchDepth.NONE

    sections = [
        "أنت عم تنفّذ طلب وصل من بوت تيليغرام مُصرَّح.",
        _language_instruction(is_arabic),
        "حافظ على نية المستخدم الأصلية. نفّذ المطلوب مباشرة، واذكر النتيجة والتحقق وأي قيود حقيقية باختصار.",
    ]
    if is_research:
        sections.append(_research_instruction(depth, is_technical))
    else:
        sections.append(
            "حسّن تنفيذ الطلب داخلياً: حدّد الهدف، القيود، ومعيار النجاح قبل العمل، "
            "بس لا تعيد صياغة الطلب للمستخدم ولا تطلب تأكيداً إلا إذا كان الغموض يمنع التنفيذ فعلاً."
        )
    sections.append(f"\n--- طلب المستخدم الأصلي ---\n{clean_text}\n--- نهاية الطلب ---")
    return EnhancedPrompt(
        text="\n\n".join(sections),
        is_arabic=is_arabic,
        is_research=is_research,
        research_depth=depth,
    )
