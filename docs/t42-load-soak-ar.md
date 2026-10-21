# T42 — Load & Soak Testing

scripts/load_soak.py يوفر harness قابل للضبط لآلاف المهام، مئات schedules، parallel owners، payload افتراضي كبير دون تخصيصه كاملًا، وslow backend delay. tracemalloc يسجل peak memory لكشف النمو غير المتناسب.

CI smoke-load يشغل 2000 task/200 schedule/50 owner مع logical payload 4 GiB ويثبت peak memory محدودًا، إضافة إلى slow-backend case.

اختبار 24–72h لا يُفرض داخل CI القصير؛ نفس harness يدعم --duration-seconds ويمكن تشغيله 86400–259200 في بيئة soak مخصصة مع metrics T29. هذا يفصل correctness gate السريع عن اختبار endurance الحقيقي طويل المدة دون الادعاء أن CI انتظر 72 ساعة.
