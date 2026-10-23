"""Arabic user-facing messages and safe error translation for the Telegram bridge."""

from __future__ import annotations

from typing import Final

import httpx

BOT_NAME: Final = "بوت OpenCode"

HELP_TEXT: Final = """الأوامر الأساسية:
/start أو /new أو /reset — جلسة جديدة
/abort أو /stop — إيقاف التنفيذ الجاري
/tasks — عرض الطلبات الحالية والمهام المجدولة
/queue — إدارة طابور الأوامر: remove <id> · up/down <id> · clear
/taskshow الرقم — تفاصيل مهمة كاملة (الحالة، الجلسة، النموذج، التكرار)
/progress — عرض تقدم الطلب الحالي
/cancel — إلغاء الطلب الحالي
/discard — حذف الملفات المعلّقة

الأوامر المباشرة والبرومبتات:
/direct الأمر الطويل — تمرير حرفي إلى الوكيل (يدمج ملفات .md المعلّقة تلقائيًا)
/daily الأمر — مهمة طويلة تُستأنف يوميًا عند 12 UTC حتى [[BRIDGE_TASK_COMPLETE]]
/dailies — عرض المهام اليومية / الإنهاء: /dailies done الرقم / الإلغاء: /dailies cancel الرقم
/prompt نفّذ الملف — تنفيذ برومبت طويل أو ملف .md مرفق حرفيًا
/topics الموضوع — نشرة مواضيع بصور حقيقية وأزرار «افتح الصفحة» لكل مصدر

الجدولة الدائمة:
/schedule الاسم | YYYY-MM-DD HH:MM | الأمر
/repeat الاسم | 1d | الأمر
/schedules — عرض كل المهام المجدولة
/schedshow الاسم — عرض تفاصيل وأمر الجدولة
/schedhistory الاسم [| العدد] — سجل التشغيل (حتى 100)
/schedrename الاسم | الاسم الجديد
/schededit الاسم | الأمر الجديد
/schedappend الاسم | نص إضافي — لإضافة أجزاء متتالية إلى أمر ضخم
/schedtime الاسم | YYYY-MM-DD HH:MM — تغيير التشغيل التالي
/schedinterval الاسم | 1d — تغيير التكرار
/schedinterval الاسم | once — تحويلها إلى تشغيل مرة واحدة
/schedrun الاسم — تشغيلها الآن دون حذف الجدولة
/schedpause الاسم — إيقاف التشغيلات المستقبلية
/schedresume الاسم — إعادة تفعيلها
/scheddelete الاسم — حذفها نهائيًا

الملفات والمرايا (حتى 4GB+):
• أرسل صورة أو فيديو أو أي ملف مع Caption لتنفيذ الأمر المرفق.
• ملف .md مع Caption (نفّذ/شغّل) يُنفَّذ محتواه كأمر مباشر.
• إذا أرسلت الملف بلا Caption، أرسل الأمر في الرسالة التالية لربطه بالملف.
/download الرابط — تنزيل حتى 8GB وإرساله مقسمًا 49MB أو عبر مرآة
/mirror الرابط — نسخ رابط إلى مرآة مجانية
/mirror file الرقم — رفع ملف منزّل (تلقائي: أصغر مرآة تتسع له)
/mirror big الرقم — تقسيم ملف 4GB+ إلى أجزاء ورفعها كلها
• المرايا: catbox (200MB) • 0x0.st (512MB) • tmpfiles/tempfile (100MB) • transfer.sh (2GB) • gofile/pixeldrain (حتى 10GB)

إدارة السيرفر (المالك في الخاص فقط):
/exec الأمر — أمر تيرمنال مباشر كما لو كتبته بنفسك (bash -lc)
/sh و /shell و /term أسماء بديلة لنفس الأمر
/menu — لوحة التحكم الكاملة (الخدمات، الموارد، النسخ، المفاتيح، التحديثات)

البحث والتحقق:
/search الطلب
/deepresearch الطلب
/extreme الطلب
/news الطلب
/compare العناصر
/factcheck الادعاء
/verify المعلومة
/open الرابط
/extract المصدر

/model — حالة النموذج
/status — حالة الجلسة
/health — فحص الوكيل
/maintenance — آخر تقرير صيانة
/config — عرض الإعدادات الفعلية غير السرية
/limits — عرض الحدود الفعلية الحالية
/metrics — قياسات حيّة للطابور والنتائج والموارد (النافذة الافتراضية 24 ساعة، ويمكن طلب نافذة أوسع: /metrics 72)
/share — إنشاء رابط مشاركة
/unshare — إلغاء رابط المشاركة
/help — عرض المساعدة

ملاحظة: أوقات /schedule و/schedtime تُكتب بصيغة UTC حاليًا."""



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
