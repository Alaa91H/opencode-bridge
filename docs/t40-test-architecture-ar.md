# T40 — Test Architecture

تم تعريف taxonomy ثابت: unit/integration/contract/e2e/load/fault/security/migration مع إبقاء test_v2/test_v3 الحالية قابلة للاكتشاف حتى لا تنكسر regression coverage أثناء النقل.

Bridge/OpenCode contract tests موجودة منذ T15، وTelegram abstraction tests موزعة على transport/UX. أضيف E2E مستقل يثبت Telegram mock → queue abstraction → OpenCode mock → output delivery دون شبكة خارجية.

الـfixtures في E2E deterministic ومحلية بالكامل، وتُنشأ لكل test دون global state. الاختبارات الحالية للDB/storage/migrations/security تبقى أدلة integration/migration/security ضمن taxonomy حتى يتم نقلها ماديًا تدريجيًا إلى subdirectories.
