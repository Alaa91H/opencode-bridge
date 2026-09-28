# T08 — واجهة إدارة الجدولة الحديثة

تضيف T08 متصفح Telegram Inline Keyboard فوق ScheduleService من دون نقل business logic إلى handlers.

## المتصفح
`/schedules` يفتح قائمة paginated. كل صف يوضح enabled/paused ويفتح صفحة إجراءات.
أزرار التنقل لا تعتمد على قائمة ثابتة، والصفحة تضبط تلقائيًا عند تغير عدد العناصر.

## الإجراءات
صفحة الجدولة تدعم pause/resume، run now، history، duplicate، edit، delete والرجوع.
الحذف ذو خطوتين ويحتاج confirmation صريح. duplicate يولد اسمًا غير متعارض ويحافظ على
prompt/timing/recurrence/timezone. جميع callbacks تحل job داخل قائمة owner الحالي ولا تقبل
الوصول إلى schedule لمستخدم آخر.

## التعديل
تبقى الأوامر النصية مدعومة للتوافق:
- /schedrename للاسم.
- /schededit للأمر.
- /schedtime للموعد التالي.
- /schedinterval للـrecurrence interval/once.
- /schedtimezone لتغيير IANA timezone مع validation.
زر Edit يعرض هذه المسارات داخل واجهة الإدارة، وبذلك لا يلغي browser أي workflow نصي موجود.

## الاختبارات
`tests/test_v2_schedule_ui.py` يثبت pagination، أزرار الإدارة المطلوبة، pause/resume state
وdelete confirmation. اختبارات T07 تستمر في تغطية persistence/timezone/DST/history/recovery.
