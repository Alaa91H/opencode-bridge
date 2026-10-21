# T36 — Security Hardening

OpenCode policy يفرض loopback فقط. systemd hardening contract يتضمن NoNewPrivileges وPrivateTmp وProtectSystem/ProtectHome وCapabilityBoundingSet فارغًا وRestrictSUIDSGID، مع تطبيقها حسب توافق deployment.

file magic inspection يتعرف على PDF/ZIP/PNG/JPEG من bytes بدل الثقة بالاسم. archive zip-bomb/traversal limits موجودة أصلًا في T13؛ T36 يضيف output jail يمنع traversal والخروج من root ورفض symlink target.

secret redaction موحد من T30/T31، وsubprocess_timeout يفرض maximum runtime حتى لو طلب caller قيمة أعلى. Sandbox T20 يبقى طبقة resource/process/network isolation الإضافية.

اختبارات T36 تغطي loopback، output traversal/symlink، magic، runtime cap، وsystemd hardening contract.
