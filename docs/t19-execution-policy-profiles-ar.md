# T19 — Execution Policy Profiles

الملفات: SAFE / DEVELOPMENT / POWER / HOST_ADMIN.

SAFE هو الافتراضي دائمًا. task override يسبق user preference، لكن HOST_ADMIN لا يُمنح إلا إذا كان owner ضمن allowlist صريحة. أي طلب أعلى من SAFE يولد EscalationAudit، سواء سُمح به أو رُفض.

Capability matrix تفصل الصلاحيات بدل الاعتماد على اسم profile داخل business logic. DEVELOPMENT يسمح build وsandbox dependency install وgit commit. POWER يضيف sandbox network/long-running. HOST_ADMIN يضيف host_admin فقط للallowlist.

sanitize_environment يحذف متغيرات تحمل مؤشرات secrets/tokens/password/API/private keys/credentials قبل تمرير environment إلى execution layer. لا تقوم هذه المرحلة بكشف قيم الأسرار في audit.
