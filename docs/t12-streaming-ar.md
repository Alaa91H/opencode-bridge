# T12 — Streaming وليس Buffering

## الضمانات

المسارات الكبيرة تعتمد chunks محدودة بدل `read()` غير المحدود أو bytes payload كامل:
- Telegram cloud download: PTB `download_to_drive`.
- Telegram Local API copy: `copy_stream`.
- LocalStorage CAS write/hash: `copy_stream`.
- S3/MinIO staging/hash: `copy_stream` مع SpooledTemporaryFile ينتقل إلى disk بعد threshold.
- Telegram upload: file handle.
- media copy وarchive-member copy: `StreamingMediaService`.

`copy_stream` يحسب SHA-256 أثناء نفس المرور فلا يحتاج قراءة ثانية. `copy_stream_async` يستخدم `asyncio.Queue(maxsize=N)` لتطبيق backpressure بين producer وconsumer.

## Cancellation

`CancellationToken` يفحص بين chunks. media/archive helpers تحذف destination الجزئي عند الإلغاء أو الخطأ. هذه cancellation خاصة بstreaming في T12؛ propagation الكامل من Telegram حتى subprocess/OpenCode له مرحلته الملزمة T24.

## قياس RAM

`tests/test_v2_streaming.py` يحاكي 3 GiB عبر VirtualLargeReader دون إنشاء payload بحجم 3 GiB. الاختبار يفرض chunk=1 MiB ويقيس `tracemalloc` ويشترط peak أقل من 8 MiB، لذلك حجم الذاكرة لا ينمو تناسبيًا مع حجم الملف.

## الاختبارات

- multi-GiB bounded-memory stream.
- sync/async cancellation.
- bounded async queue/backpressure.
- streaming SHA-256.
- media chunk iteration.
- archive cancellation partial-file cleanup.
- LocalStorage/S3/Telegram transport suites السابقة تستمر كاختبارات تكامل/regression.

## حدود المرحلة

T12 لا تنفذ parsing/normalization/transcription أو safe archive extraction policy؛ هذه T13. كما لا تستبدل resource scheduler أو cancellation end-to-end المقررين في T22/T24.
