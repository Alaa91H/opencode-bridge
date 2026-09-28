# T27 — Telegram UX v2

UXRenderer يوفر view model مستقلًا عن Telegram SDK يضم pagination وbreadcrumbs وinline buttons وconfirmation views. callback data موحدة تحت namespace `ux:` لتقليل تشتت البروتوكول.

TelegramBrowsers يوفر task history وoutput browser وschedule browser. صفحة المهمة تعرض retry/cancel/duplicate/rerun/download حسب الحالة، وoutput browser يعرض download. الإجراءات المدمرة تستخدم confirmation view قبل التنفيذ.

pagination تتعامل مع القوائم الفارغة والصفحات الخارجة عن المجال بصورة مستقرة. breadcrumbs تصف المسار Home → القسم → الكيان.

هذا layer إضافي ولا يزيل command handlers النصية الحالية؛ الأوامر النصية تبقى مسار compatibility مدعومًا بالكامل، ويمكن callbacks استدعاء services نفسها بدل نسخ business logic داخل Telegram layer.

اختبارات T27 تغطي pagination، breadcrumbs، الأزرار المطلوبة، confirmations، task/output/schedule browsers، والقوائم الفارغة.
