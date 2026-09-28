# T11 — Attachment Storage v2

## البنية

طبقة التخزين مستقلة عن Telegram وتتكون من:
- `StorageBackend`: عقد put/open/exists/delete/local_path.
- `LocalStorage`: CAS محلي.
- `S3Storage`: backend اختياري يقبل عميل S3-compatible، لذلك يمكن استخدام AWS S3 أو MinIO دون جعل boto3 اعتمادًا إلزاميًا.
- `AttachmentMetadataStore`: metadata والمراجع الدائمة في SQLite.
- `AttachmentStorageService`: ingest/release/retention/orphan cleanup.

## Content-addressed storage وdeduplication

كل blob يعرّف بـSHA-256. LocalStorage يقرأ المصدر chunks، يحسب SHA-256 أثناء الكتابة إلى ملف مؤقت، ثم ينقل الملف atomically إلى مسار مشتق من hash. إذا كان blob موجودًا لا تنشأ نسخة ثانية.

S3Storage يحسب hash من stream ويختبر وجود object قبل الرفع. لا تُستخدم أسماء المستخدم كمفاتيح تخزين.

## Reference counting

`storage_blobs.reference_count` يمثل عدد `attachment_refs` الحية. إضافة reference وزيادة العداد تتم داخل transaction واحدة. release يتحقق من owner، يحذف reference ويخفض العداد؛ وعند الصفر يستطيع service حذف blob والmetadata.

## Metadata

كل reference يدعم:
- Telegram `file_id` و`file_unique_id`.
- SHA-256/storage key.
- claimed MIME وdetected MIME.
- size.
- owner وtask اختياري.
- retention deadline.
- scan state.

Binary content لا يخزن في SQLite.

## Cleanup policies

`cleanup_expired` يحرر المراجع التي انتهت retention الخاصة بها. `cleanup_orphans` يحذف blobs ذات reference_count=0. الحذف يمر عبر backend abstraction.

## التوافق

`AttachmentStore` القديم يبقى compatibility ingestion surface في هذه المرحلة؛ T11 تقدم storage service مستقلًا يمكن لمسارات ingestion/task lifecycle استدعاؤه دون إدخال SQL أو backend logic إلى Telegram handlers. إزالة الازدواجية النهائية مؤجلة إلى T46 حسب الخطة.

## الاختبارات

- Local CAS round-trip وdedup.
- reference counting وowner isolation.
- retention cleanup.
- schema metadata completeness.
- S3/MinIO-compatible round-trip/dedup/delete/hash validation عبر fake client بلا network.
