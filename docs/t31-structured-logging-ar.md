# T31 — Logging احترافي

JsonFormatter ينتج JSON structured logs ويغطي event/task/schedule/owner_hash/trace/attempt/component/duration/status. owner لا يسجل خامًا بل SHA-256 مختصرًا.

safe_extra يحذف prompt/file_content/attachment_content افتراضيًا ثم يطبق redaction المشترك على secrets. attributes في LogRecord تمر أيضًا عبر redaction قبل serialization.

build_logger يستخدم RotatingFileHandler بسياسة max-bytes وعدد backups محدودين وقابلين للضبط؛ بذلك تكون rotation/retention bounded ولا ينمو الملف بلا حد.

اختبارات T31 تثبت الحقول السياقية، owner hashing، عدم تسجيل prompt/file content عبر API الآمنة، secret redaction، وسياسة rotation/retention.
