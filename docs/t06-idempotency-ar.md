# T06 — Idempotency ومنع التكرار

## نموذج التسليم

Telegram ومشغلات الجدولة تعامل كمدخلات **at-least-once**. لا نفترض أن المصدر لن يعيد
التسليم. بدلًا من ذلك يحول Bridge إنشاء المهمة إلى **effectively-once** عند حدود SQLite.

## Telegram updates

المفتاح الدائم هو `scope=telegram_update` مع `update.update_id`.
`TaskApplicationService` يمرر المفتاح إلى `TaskQueueStore.enqueue_once`.
داخل `BEGIN IMMEDIATE` يتم فحص `idempotency_records` ثم إنشاء المهمة والسجل في نفس
المعاملة. إعادة نفس update تعيد المهمة الأصلية ولا تنشئ صف `agent_tasks` جديدًا.

## Restart safety

`idempotency_records` و`agent_tasks` محفوظان في SQLite نفسها، لذلك restart لا يمحو
قرار commit. الاختبار يغلق Store بالكامل، يفتحه من جديد، يعيد نفس update key ويتحقق أن
عدد المهام بقي واحدًا وأن task id لم يتغير.

## Schedule occurrences

`schedule_occurrences(schedule_key, occurrence_key)` له primary key مركب.
`claim_schedule_occurrence` يستخدم `INSERT OR IGNORE`؛ أول tick فقط يحصل على claim.
الاختبار يثبت رفض tick المكرر قبل restart وبعده.

## Atomicity

إن فشل إنشاء task أو idempotency record قبل commit يتم rollback للمعاملة كاملة، فلا يبقى
record يشير إلى task غير committed. مسار duplicate يقرأ المهمة داخل lock نفسه ولا يعيد
دخول lock عبر `get()`.

## حدود T06

سياسات timezone/misfire/overlap وتوليد occurrence keys للـscheduler الحديث تخص T07.
T06 توفر primitive durable لمنع تكرار occurrence ولا تقدم Scheduler Engine v2 قبل ترتيبها.
