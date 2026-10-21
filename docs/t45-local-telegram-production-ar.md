# T45 — Local Telegram API كخيار Production

cloud يبقى default. deploy/telegram-local-api.env.example يوفر profile مستقل local على loopback مع file root واضح وتعليمات تشغيل service كمستخدم غير مميز مع systemd hardening.

capability detection الأساسية موجودة منذ T10 عبر TelegramTransportSettings ويمكن لـ/health T32 عرض mode/capability من نفس transport state بدل افتراض local.

streaming download/upload من T10/T12 هو المسار المطلوب للملفات الكبيرة؛ اختبار T45 يستخدم logical 4 GiB shape و64 KiB chunks دون تخصيص الملف كاملًا في RAM.

تشغيل Telegram Local Bot API binary/container نفسه مسؤولية deployment environment؛ Bridge لا يثبت dependency عشوائيًا على host، اتساقًا مع T13/T20/T36.
