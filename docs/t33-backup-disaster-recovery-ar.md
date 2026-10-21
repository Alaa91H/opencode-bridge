# T33 — Backup & Disaster Recovery

BackupService يستخدم SQLite online backup API عبر اتصال قاعدة البيانات الحي، لذلك لا ينسخ ملف WAL/DB نسخًا خامًا أثناء الكتابة. النسخة تشمل الجداول الدائمة مثل schedules وuser_settings وmetadata؛ الإعدادات السرية الخارجية لا تُضاف إلى backup من هذه الطبقة.

كل backup ينتج manifest يحوي SHA-256 والحجم ووقت الإنشاء وأهداف RPO/RTO. verify يتحقق من checksum/size ثم PRAGMA integrity_check. restore يستخدم SQLite backup API إلى قاعدة جديدة، والاختبار الآلي يثبت استعادة user_settings من النسخة.

الأهداف الافتراضية: RPO ساعة وRTO 15 دقيقة، ويمكن ضبطهما حسب deployment. encrypt callback اختياري لإنشاء encrypted artifact عندما تتطلب سياسة البيئة ذلك، دون فرض خوارزمية أو secret store داخل طبقة DB.

اختبارات T33 تغطي online backup، manifest، integrity verification، automated restore، persistence الفعلي للmetadata، وencryption hook.
