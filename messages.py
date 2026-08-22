"""Arabic user-facing messages and safe error translation for the Telegram bridge."""

from __future__ import annotations

from typing import Final

import httpx

BOT_NAME: Final = "بوت OpenCode"

HELP_TEXT: Final = """الأوامر المتاحة:
/start أو /new — جلسة جديدة
/reset — إعادة ضبط الجلسة
/abort أو /stop — إيقاف المهمة الحالية
/tasks — عرض المهام
/progress [رقم_المهمة] — التقدم الحي للمهمة الحالية
/trace رقم_المهمة — سجل نشاط آمن ومختصر لمهمة
/cancel رقم_المهمة — إلغاء مهمة
/schedule الوقت | الطلب — جدولة مهمة
/repeat 1d | الطلب — مهمة متكررة

البحث والتحقق:
/search الطلب — بحث موثّق سريع
/deepresearch الطلب — بحث عميق متعدد المصادر
/extreme الطلب — بحث شديد العمق
/news الطلب — أخبار حديثة
/compare العناصر — مقارنة موثّقة
/factcheck الادعاء — تدقيق ادعاء
/verify المعلومة — تحقق محدد
/open الرابط — فحص رابط أو مصدر
/extract المصدر — استخراج بيانات

/model — عرض أو تغيير النموذج
/status — حالة الجلسة
/health — فحص الوكيل
/maintenance — آخر تقرير صيانة
/share — إنشاء رابط مشاركة
/unshare — إلغاء رابط المشاركة
/help — عرض هالمساعدة"""


def user_error(exc: Exception, operation: str = "معالجة الطلب") -> str:
    """Return an Arabic error message without exposing exception internals."""
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return "تعذّر الاتصال بوكيل OpenCode. سيتابع البوت المحاولة تلقائيًا؛ أعد المحاولة بعد لحظات."
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "استغرق الوكيل وقتًا أطول من المتوقع في تنفيذ الطلب. يمكنك الانتظار قليلًا أو استخدام /abort ثم إعادة المحاولة."
    if isinstance(exc, httpx.TimeoutException):
        return "انتهت مهلة الاتصال بالوكيل قبل اكتمال الطلب. أعد المحاولة لاحقًا."
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "رفض الوكيل طلب البوت بسبب إعدادات المصادقة. راجع إعدادات الاتصال بالخادم."
        if code == 404:
            return "تعذّر العثور على المورد المطلوب لدى الوكيل. أنشئ جلسة جديدة باستخدام /new ثم أعد المحاولة."
        if code == 409:
            return "الجلسة مشغولة حاليًا. استخدم /abort لإيقاف الطلب السابق أو انتظر حتى يكتمل."
        if code == 429:
            return "تم تجاوز حد الطلبات المؤقت للوكيل. انتظر قليلًا ثم أعد المحاولة."
        if code >= 500:
            return "واجه وكيل OpenCode مشكلة داخلية. سيستمر البوت في العمل؛ أعد المحاولة بعد لحظات."
        return f"تعذّر {operation} لأن الوكيل أعاد رمز الحالة {code}."
    return f"حدثت مشكلة غير متوقعة أثناء {operation}. تم تسجيل التفاصيل داخليًا دون عرض بيانات حساسة."


def build_blocked_message(description: str) -> str:
    return (
        "لم يُنفَّذ الطلب لأنه يتضمن عملية بناء أو تجميع على الخادم "
        f"({description}). ابنِ واختبر الحزمة خارج الخادم، ثم انشر الملفات الجاهزة فقط."
    )


def empty_response_message() -> str:
    return "لم يُرجع الوكيل ردًا نصيًا. أعد صياغة الطلب أو أنشئ جلسة جديدة باستخدام /new."


def unauthorized_message() -> str:
    return "غير مصرح لك باستخدام هذا البوت."


def startup_message() -> str:
    return "أهلًا فيك. عملتلك جلسة جديدة؛ ابعت طلبك وببلّش فيه."
