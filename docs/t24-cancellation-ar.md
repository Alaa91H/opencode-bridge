# T24 — Cancellation حقيقية

CancellationToken هو العقد المشترك من Telegram/TaskService حتى OpenCode/subprocess/media/upload. كل مرحلة طويلة تستدعي checkpoint قبل/بين وحدات العمل، ويمكنها تسجيل cleanup callback. cancel idempotent ويشغل cleanup بترتيب LIFO مرة واحدة.

run_cancellable_process يراقب process وtoken بالتوازي. عند الإلغاء يرسل TERM، ينتظر grace period، ثم KILL عند الحاجة، وبعدها ينتظر process.wait دائمًا لمنع zombie. finally يضمن عدم ترك process حيًا عند exception.

اختبارات T24 تغطي checkpoints لجميع مراحل المسار المطلوبة، cooperative wait، cleanup order/idempotency، TERM الطبيعي، وKILL escalation مع reap.

هذا العقد لا يجعل APIs الخارجية قابلة للإلغاء قسرًا بذاتها؛ adapters يجب أن تربط token بإغلاق stream/request الخاص بها عند التكامل.
