# T20 — Build Sandbox

الاختيار runtime-based: rootless Podman إن توفر، ثم bubblewrap. إذا لم يتوفر backend مدعوم تفشل العملية صراحة؛ لا يوجد fallback لتنفيذ build أو dependency install على host.

Podman يستخدم user namespace، root filesystem read-only، workspace mount محدد، tmpfs bounded، CPU/memory/PIDs limits، وnetwork=none افتراضيًا. Bubblewrap يعزل user/pid/ipc/uts، وnetwork namespace عند تعطيل الشبكة، ويربط فقط أدوات النظام read-only والworkspace المطلوبة.

wall-time يطبق في runner ويقتل العملية عند timeout. Podman --rm وbubblewrap ephemeral namespaces يحققان cleanup بعد الخروج. واجهات Python/pytest وnpm وGradle تمر عبر runner نفسه.

اختبارات security تتحقق من عدم mount لـDocker socket أو /home أو /etc كمساحة قابلة للوصول، ومن رفض backend غير معروف وعدم وجود host fallback.

ملاحظة: disk_mb يحد tmpfs scratch في Podman؛ quota كاملة للworkspace تعتمد على filesystem/container storage وتبقى ضمن Resource Scheduler/Workspace stages اللاحقة.
