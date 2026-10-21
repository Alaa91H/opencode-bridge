# T05 — Durable Queue v2

## هوية المهمة والحالة

كل مهمة جديدة تحصل على UUID عام `public_id`، ويمكن أن تحمل `idempotency_key`.
الـstate machine المدعومة في 2.0 هي:
`draft → queued → leased → running → waiting/retrying → completed|failed|cancelled|dead_letter`.
تبقى حالات legacy المقروءة متوافقة أثناء نافذة الترحيل.

## Lease وheartbeat

`claim_next(worker_id, lease_seconds)` يعمل داخل transaction فورية، يعيد أولًا أي
`leased/running` انتهت lease الخاصة بها إلى queue، ثم يحجز مهمة واحدة قابلة للتنفيذ.
`heartbeat` يمدد lease فقط عندما يطابق worker المالك، و`mark_running` يمنع worker آخر
من تحويل lease لا يملكها إلى running.

## Retry وDLQ

`retry_or_dead_letter` يميز retryable عن non-retryable. الأخطاء المؤقتة تستخدم exponential
backoff مع jitter، وRetry-After إن كان أكبر من التأخير المحسوب. عند تجاوز max attempts أو
الخطأ غير القابل لإعادة المحاولة تنتقل المهمة إلى `dead_letter`.

## الإدارة

- `/failed`: يعرض failed/dead_letter الخاصة بالمستخدم مع UUID العام.
- `/retry <task-id|uuid>`: يعيد مهمة يملكها المستخدم إلى queued ويوقظ workers.
- لا يسمح الأمر بإعادة مهمة مستخدم آخر.

## Recovery

بيانات lease/attempt/checkpoint محفوظة في SQLite. restart لا يحذف المهمة committed.
عند انتهاء lease بعد موت worker تصبح المهمة قابلة للاسترداد من worker جديد، ويزداد attempt.
الاختبارات تغطي انتهاء lease، heartbeat، الاسترداد، retry، DLQ، إعادة المهمة، وuniqueness
لـidempotency key.

## حدود المرحلة

T05 توفر مفتاح idempotency وتفرض uniqueness له، لكن إنشاء سجل لكل Telegram update ومنع
duplicate schedule occurrence/enqueue هو نطاق T06 ولا يتم تقديمه هنا قبل أوانه.
