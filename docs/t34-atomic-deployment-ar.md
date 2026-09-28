# T34 — Atomic Deployment

AtomicDeployment يستخدم releases/<version> وcurrent symlink. ReleaseArtifact يحمل version/source/SHA-256؛ checksum يتحقق قبل إنشاء release.

التسلسل التنفيذي: verify → materialize release → test → migrate DB callback → health check → atomic symlink switch عبر os.replace → restart → smoke test. أي failure قبل switch يترك current كما هو. failure بعد switch في restart/smoke يعيد current إلى الإصدار السابق ويعيد restart تلقائيًا.

download يبقى مسؤولية مصدر artifact/adapter، بينما pipeline يبدأ من artifact محلي موثّق checksum حتى لا تربط deployment core ببروتوكول تنزيل بعينه.

اختبارات T34 تثبت pipeline الكامل، atomic current switch، رفض checksum غير الصحيح، وautomatic rollback مع restart ثانٍ عند فشل smoke.
