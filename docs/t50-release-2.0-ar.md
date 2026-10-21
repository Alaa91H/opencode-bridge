# T50 — إصدار OpenCode Bridge 2.0

T50 لا تُغلق قبل اكتمال كل أدلة release-evidence-2.0.json ونجاح tag/release v2.0.0.

كل بنود CI/Python matrix/migration/backup/crash/schedule/duplicate/streaming/Local Bot API/long prompt/multi-workspace/security/rollback/docs لها أدلة من T00–T49. البند الوحيد غير القابل للاختصار داخل جلسة تنفيذ قصيرة هو soak حقيقي 24–72 ساعة.

لذلك يبقى VERSION=1.8.0 ولا يُنشأ tag 2.0.0 الآن. Release workflow يشغّل scripts/release_readiness.py تلقائيًا عندما يصبح VERSION=2.0.0، والبوابة fail-closed: duration أقل من 24 أو أكثر من 72، soak غير مكتمل، أو أي evidence ناقص يمنع الإصدار.

بعد تشغيل `scripts/load_soak.py --duration-seconds 86400` أو مدة حتى 259200 في بيئة endurance فعلية، يجب تسجيل النتيجة الفعلية في evidence فقط إذا اكتملت دون failure/leak خارج thresholds. بعدها يُرفع VERSION إلى 2.0.0، تُضاف release notes، تمر جميع البوابات، ثم Release workflow ينشئ tag/release.

لا يجوز تعليم T50 مكتملة قبل ذلك.
