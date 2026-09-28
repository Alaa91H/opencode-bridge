# T09 — Draft / Prompt Editor

يوفر Draft Editor مساحة دائمة لبناء أوامر كبيرة من عدة رسائل وملفات قبل تشغيلها أو جدولتها.

## الاستخدام
- `/draft new NAME`: إنشاء Draft وجعله active.
- أي رسالة نصية لاحقة تضاف كنسخة جديدة.
- أي ملف/صورة/صوت/فيديو يمر من attachment intake ويضاف metadata إلى الـDraft مع caption.
- `/draft show NAME`: عرض النسخة الحالية.
- `/draft clear NAME`: إنشاء نسخة فارغة جديدة مع الاحتفاظ بالتاريخ.
- `/draft save NAME`: حفظ وإغلاق وضع الالتقاط الحالي.
- `/draft run NAME`: enqueue عبر TaskApplicationService مع idempotency.
- `/draft schedule NAME | SCHEDULE | WHEN`: إنشاء schedule من prompt الحالي.

## الاستمرارية والإصدارات
الجداول `drafts` و`draft_versions` في SQLite تحفظ current version وكل snapshot سابق. لا تعتمد
المحتويات على ذاكرة process، لذلك تبقى بعد restart. حالة active draft في Telegram user_data هي
راحة UX فقط؛ الـDraft نفسه وإصداراته دائمة.

## الأحجام
النص محفوظ كـSQLite TEXT ولا يوجد قص اصطناعي في DraftStore. اختبار T09 يثبت prompt بحجم 1 MiB
مع restart. الملفات تستخدم attachment intake الحالي ويخزن الـDraft metadata/path؛ نقل التخزين
إلى StorageBackend وstreaming الكامل يتم بالترتيب في T11/T12، ولا تتم مضاعفة binary داخل SQLite.

## الحدود المعمارية
Telegram adapter رفيع، DraftService يملك use cases، وDraftStore وحده يملك SQL. run يمر عبر
TaskApplicationService، وschedule يمر عبر ScheduleService.
