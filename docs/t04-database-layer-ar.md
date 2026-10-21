# T04 — Database Layer v2

## الطبقة الموحدة

`BridgeDatabase` هو مالك اتصال SQLite المشترك وإعداداته: WAL، `synchronous=NORMAL`،
`foreign_keys=ON`، `busy_timeout`، transaction lock، checkpoint وintegrity check.
تقبل الـstores حقن نفس `BridgeDatabase` مع بقاء constructors القديمة متوافقة.

## Migrations

`MigrationRunner` ينشئ `schema_migrations(version, name, checksum, applied_at)` ويطبق كل
migration داخل `BEGIN IMMEDIATE`. اختلاف checksum لمigration مطبقة يوقف التشغيل بدل تعديل
التاريخ بصمت. DDL الخاص بطابور المهام والأعمدة القديمة أصبح migration صريحة وليس ALTER داخل init.

T04 ينشئ الجداول: `task_attempts`, `task_events`, `attachments`, `task_outputs`,
`schedules`, `schedule_runs`, `agent_sessions`, `user_settings`,
`resource_snapshots`, `failure_records`.

## Index/query-plan policy

الـindexes الخاصة بالـqueue والدورات والـattachments مبنية على الاستعلامات الفعلية.
`tests/test_v2_database_query_plans.py` يستخدم `EXPLAIN QUERY PLAN` لمنع regression إلى full
scan في المسارات الحرجة.

## Checkpoint وintegrity

توفر الطبقة `checkpoint()` مع PASSIVE/FULL/RESTART/TRUNCATE، و`periodic_integrity_check()`
بفاصل افتراضي 24 ساعة حتى لا يصبح الفحص الكامل تكلفة لكل عملية.

## خطة rollback

1. قبل migration إنتاجية يؤخذ snapshot/backup متسق لملف SQLite مع ملفات WAL/SHM حسب آلية النشر.
2. migrations في T04 additive فقط ولا تحذف أو تعيد تفسير بيانات legacy.
3. عند فشل migration تُلغى transaction تلقائيًا ولا يُسجل رقمها في `schema_migrations`.
4. عند اكتشاف checksum mismatch يتوقف التشغيل؛ ممنوع تعديل migration منشورة. يُضاف version جديد.
5. للرجوع إلى binary أقدم ضمن نافذة T04 تبقى الجداول/الأعمدة الإضافية غير مؤذية لأنه لا يوجد DROP/rename.
6. إذا احتاج rollback استعادة schema/data، يوقف الكاتب، تحفظ نسخة الملف الفاشل للتشخيص، ثم يستعاد
   snapshot السابق وتجرى `PRAGMA integrity_check` قبل إعادة الخدمة.
7. T35 سيضيف سياسة rollback/restore للنشر الذاتي والمigrations الخطرة؛ T04 لا يقدمها مبكرًا.

## التحقق

اختبارات T04 تغطي pragmas، commit/rollback، checkpoint، integrity، idempotent migrations،
الجداول المطلوبة وquery plans. يجب نجاح CI الكامل على Python 3.12/3.13/3.14 قبل إغلاق T04.
