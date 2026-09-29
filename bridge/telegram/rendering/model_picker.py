"""Inline model and reasoning-level picker rendering.

Callback payloads stay short by design: Telegram allows only 64 bytes of
``callback_data`` and a qualified OpenCode model ID alone is 30-45 characters.
Buttons therefore carry only an index into the live ranked catalog, and the
resolver re-reads the catalog when the button is pressed instead of trusting a
name that a user could have edited.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CALLBACK_PREFIX = "mdl"
CALLBACK_PATTERN = r"^mdl:"
MODELS_PER_PAGE = 6
VARIANTS_PER_ROW = 1
AUTO_TEXT = "🔄 تلقائي (اختيار البوت)"
BACK_TEXT = "‹ رجوع للنماذج"
AUTO_LEVEL_TEXT = "Default — الافتراضي (تلقائي)"
NUMBER_EMOJI = "0123456789"
KEYCAP = "⃣"


def callback_model(index: int) -> str:
    return f"{CALLBACK_PREFIX}:m:{index}"


def callback_variant(model_index: int, variant_index: int) -> str:
    return f"{CALLBACK_PREFIX}:v:{model_index}:{variant_index}"


def callback_auto() -> str:
    return f"{CALLBACK_PREFIX}:auto"


def callback_back() -> str:
    return f"{CALLBACK_PREFIX}:back"


def callback_noop() -> str:
    return f"{CALLBACK_PREFIX}:noop"


def model_page(view: object, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Render one page of selectable models with an automatic-choice escape."""
    choices = tuple(getattr(view, "choices", ()))
    if not choices:
        return "لا توجد نماذج مجانية نشطة حاليًا.", InlineKeyboardMarkup([])
    pages = max(1, (len(choices) + MODELS_PER_PAGE - 1) // MODELS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    current = getattr(view, "current_model", None)
    rows: list[list[InlineKeyboardButton]] = []
    for offset, choice in enumerate(choices[page * MODELS_PER_PAGE:(page + 1) * MODELS_PER_PAGE]):
        index = page * MODELS_PER_PAGE + offset
        mark = "✅ " if choice.model_id == current else ""
        rows.append([InlineKeyboardButton(f"{mark}{choice.label}", callback_data=callback_model(index))])
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton("‹ السابق", callback_data=f"{CALLBACK_PREFIX}:p:{page-1}"))
    navigation.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=callback_noop()))
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton("التالي ›", callback_data=f"{CALLBACK_PREFIX}:p:{page+1}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(AUTO_TEXT, callback_data=callback_auto())])
    return summary_text(view), InlineKeyboardMarkup(rows)


def variant_levels(view: object, model_index: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render one model index's reasoning levels, strongest first, one per row."""
    choice = view.model_at(model_index)
    if choice is None:
        return "هذا النموذج لم يعد متاحًا في الكتالوج.", InlineKeyboardMarkup([])
    rows: list[list[InlineKeyboardButton]] = []
    current_variant = getattr(view, "current_variant", None)
    buttons = [InlineKeyboardButton(AUTO_LEVEL_TEXT, callback_data=callback_variant(model_index, 0))]
    for offset, variant_id in enumerate(choice.variants, start=1):
        label = variant_label(variant_id)
        if variant_id == current_variant:
            label = f"✅ {label}"
        buttons.append(
            InlineKeyboardButton(f"{rank_prefix(offset)} {label}", callback_data=callback_variant(model_index, offset))
        )
    for start in range(0, len(buttons), VARIANTS_PER_ROW):
        rows.append(buttons[start:start + VARIANTS_PER_ROW])
    rows.append([InlineKeyboardButton(BACK_TEXT, callback_data=callback_back())])
    count = len(choice.variants)
    return (
        f"مستويات الاستدلال المتاحة في «{choice.label}» ({count}):\n"
        "مرتبة من الأقوى للأضعف — اضغط على المستوى اللي عايزه.",
        InlineKeyboardMarkup(rows),
    )


def rank_prefix(position: int) -> str:
    """Number a level so the strongest-to-weakest order is visible in one list."""
    if 1 <= position <= 10:
        return f"{NUMBER_EMOJI[position]}{KEYCAP}"
    return f"{position}."


def variant_label(variant_id: str) -> str:
    from bridge.services.model_selection_service import VARIANT_LABELS

    return VARIANT_LABELS.get(variant_id.casefold(), variant_id)


def summary_text(view: object) -> str:
    current = getattr(view, "current_model", None) or "غير محدد"
    current = current.split("/", 1)[-1] if isinstance(current, str) else str(current)
    variant = getattr(view, "current_variant", None) or "افتراضي"
    if getattr(view, "pinned", False):
        mode = "اختيار يدوي مثبّت (مش هيتغيّر تلقائيًا)"
    else:
        mode = "اختيار تلقائي (البوت بيختار الأقوى يوميًا)"
    return (
        "⚙️ اختيار النموذج ومستوى الاستدلال\n\n"
        f"• الحالي: {current}\n"
        f"• مستوى الاستدلال: {variant}\n"
        f"• الوضع: {mode}\n\n"
        "اختر النموذج من الأزرار، وبعدها تختار مستواه. أو اضغط «تلقائي» عشان ترجّع "
        "لاختيار البوت."
    )


def confirmed_text(view: object, *, model_label: str, variant_label_text: str) -> str:
    header = summary_text(view)
    return f"{header}\n\n✅ تم اعتماد: {model_label} — {variant_label_text}"
