# T10 — إزالة قيود Telegram للملفات الكبيرة

## الهدف

يوفر OpenCode Bridge مسارين متوافقين للنقل: Telegram Bot API السحابي الافتراضي وTelegram Local Bot API الاختياري، مع إبقاء payloads الكبيرة على القرص بدل تحويلها إلى bytes في الذاكرة.

## الإعداد

- `TELEGRAM_API_MODE=cloud` هو الافتراضي ويحافظ على السلوك السابق.
- `TELEGRAM_API_MODE=local` يفعّل PTB local mode.
- `TELEGRAM_LOCAL_API_BASE_URL` يحدد Bot API base URL.
- `TELEGRAM_LOCAL_FILE_BASE_URL` يحدد file base URL.
- القيم غير `cloud|local` مرفوضة عند تحميل BridgeSettings.

## Capability detection

`bridge/infrastructure/telegram/capabilities.py` يحول الإعداد الفعلي إلى TelegramCapabilities: mode، local_paths، streaming_download، streaming_upload، وعناوين النقل عند local mode. لا تعتمد بقية الطبقات على تخمين متغيرات البيئة.

## تنزيل الملفات

في cloud mode يستخدم AttachmentStore واجهة PTB `download_to_drive` إلى مسار مُدار.

في local mode، عندما يعيد Local Bot API `file_path` محليًا صالحًا، ينسخ AttachmentStore المصدر إلى مساحة incoming المُدارة بواسطة `shutil.copyfileobj` بكتل 1 MiB. لا يوجد `read()` شامل ولا `download_as_bytearray`.

بعد التنزيل/النسخ يحسب SHA-256 أيضًا على chunks بحجم 1 MiB.

## رفع الملفات

TelegramExecutionDelivery يفتح output كـ file handle ويمرره إلى `send_document`، ولا يحول الملف إلى bytes/bytearray. بذلك يستطيع HTTP transport/PTB streaming الملف من القرص.

## الاختبارات

`tests/test_v2_telegram_transport.py` يثبت:
- cloud default.
- local capability detection.
- رفض mode غير صالح.
- local file path لا يستدعي remote download.
- ملف local متعدد MiB ينسخ إلى disk.
- cloud يستعمل download_to_drive.
- output upload يستعمل file handle لا bytes.

## حدود المرحلة

T10 لا تنشئ StorageBackend أو S3/MinIO أو CAS/dedup/reference counting؛ هذه مسؤولية T11. كما أن load/backpressure/cancellation متعددة GB الموسعة مؤجلة صراحة إلى T12/T45 وفق الخطة الملزمة.
