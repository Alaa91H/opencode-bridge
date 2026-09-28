# T22 — Resource Scheduler v2

كل ResourceTask يحمل ResourceCost مستقلًا لـCPU/RAM/disk/I/O/model/media. ResourceCapacity تمثل الميزانية القصوى، وResourcePressure يدخل القياس الحالي للـCPU/RAM/disk/I/O في قرار admission.

ResourceScheduler يحسب السعة المتبقية مع RAM/disk safety headroom، ثم يطبق dominant-resource best-fit-decreasing بصورة deterministic لتقليل fragmentation. بعد قبول كل مهمة تُطرح تكلفتها من جميع الأبعاد، لذلك لا يمكن oversubscribe لبعد مخفي مثل model أو media slots.

ram_guard وdisk_guard يمنعان جدولة workload يستهلك كامل الذاكرة أو القرص حتى عندما تكون القيمة الاسمية متاحة. الاختبارات تغطي mixed workloads، ضغط CPU الحالي، OOM headroom، disk exhaustion، واستقلال model/media capacity.
