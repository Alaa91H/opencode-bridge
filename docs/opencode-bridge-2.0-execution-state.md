# OpenCode Bridge 2.0 — حالة التنفيذ

> المرجع الملزم: `docs/opencode-bridge-2.0-execution-plan-ar.md`

## المرحلة الحالية

- المرحلة المغلقة الأخيرة: **T08 — واجهة إدارة الجدولة الحديثة**
- حالة T00: **مكتملة**
- حالة T01: **مكتملة**
- حالة T02: **مكتملة**
- المرحلة التالية المسموحة فقط: **T09 — Draft / Prompt Editor للأوامر الضخمة**
- قاعدة الانتقال: لا يجوز بدء T10 قبل إغلاق T09 بالكامل بنفس الصرامة.

## تقدم T00

- [x] إنشاء وثيقة Architecture Baseline للحالة الحالية.
- [x] تسجيل جميع جداول SQLite الحالية ومخططها.
- [x] تسجيل جميع متغيرات البيئة الحالية، مع فصل الإعدادات الموثقة عن مراجع الصيانة/الداخلية.
- [x] حصر أوامر Telegram الحالية في مسار الإنتاج الفعلي `run_v3`.
- [x] حصر جميع خدمات systemd والمؤقتات.
- [x] تسجيل حدود المرفقات الحالية.
- [x] تسجيل سياسات OpenCode الحالية.
- [x] إنشاء اختبارات regression للوظائف الأساسية قبل refactor.
- [x] إنشاء benchmark baseline للذاكرة والـCPU وقاعدة البيانات.
- [x] توثيق نتائج baseline وCI على commit إغلاق التنفيذ البرمجي لـT00.

## أدلة T00

### المرجع والخطة
- ملف الخطة: `docs/opencode-bridge-2.0-execution-plan-ar.md`
- commit إنشاء المرجع: `049020b62db93180dcde9155f74df19f367d8227`

### وثيقة baseline
- `docs/v2-baseline-architecture-ar.md`
- توثق production entrypoint، architecture، الجداول، env، Telegram commands، systemd، attachment limits، OpenCode policy، workers وCI.

### Regression baseline
- `tests/test_v2_baseline_contract.py`
- يثبت production `run_v3` path، الجداول الستة، attachment limits، OpenCode deny policy، schedule API، systemd units، وsingle-message V3 task lifecycle.

### Probe / benchmark
- `scripts/v2_baseline_probe.py`
- inventory: modules/tests/commands/environment references/SQLite/systemd/attachments/OpenCode policy.
- benchmark: CPU SHA-256 throughput + peak RSS + SQLite WAL inserts + indexed point selects.

### إصلاح baseline لازم قبل التثبيت
- تم إصلاح `v3_plugin.py` كي لا يعرض task ID أو queue position وكي يستخدم نفس رسالة الحالة الواحدة.
- commit: `eb0fcce01c26ac47db4794d2fc6c73cb9bae8308`

### CI
- commit التنفيذ المتحقق منه: `840c25f15dad17cf31cda15a4353380e686bcd9d`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36353649290
- النتيجة: **success**
- نجح:
  - Install runtime dependencies
  - Validate Python syntax
  - Run unit tests
  - Record v2 baseline inventory
  - Run v2 baseline micro-benchmark
  - Validate shell maintenance scripts
  - Validate OpenCode configuration
  - Validate V3 OpenCode configuration
  - Validate version

## سجل التنفيذ

### 2026-09-27 — T00
1. تم إنشاء الخطة المرجعية الدائمة.
2. تم إنشاء ملف حالة التنفيذ.
3. تم جرد production path الحقيقي `python -m run_v3`.
4. تم جرد SQLite وTelegram commands وsystemd وenvironment وattachments وOpenCode policies.
5. تم اكتشاف مسار V3 قديم ما زال يعرض أرقام الطابور؛ تم إصلاحه قبل تثبيت baseline.
6. تم إنشاء architecture baseline.
7. تم إنشاء regression contract.
8. تم إنشاء reproducible inventory + micro-benchmark probe.
9. تم تشغيل probe وbenchmark داخل CI.
10. نجح CI بالكامل على commit `840c25f15dad17cf31cda15a4353380e686bcd9d`.
11. **T00 مغلقة.**

## الخطوة التالية المسموحة فقط

بدء **T01 — تحديث Runtime والاعتماديات** بقراءة ملف الخطة كاملًا أولًا، ثم:
1. تثبيت الإصدارات المستقرة الحالية من المصادر الرسمية.
2. إضافة Python 3.12/3.13/3.14 CI matrix.
3. ترقية الاعتماديات stable فقط.
4. إضافة reproducible dependency constraints/lock.
5. Dependabot أو Renovate.
6. upgrade tests وإزالة deprecation warnings الحرجة.
7. عدم بدء T02 حتى نجاح كامل Acceptance T01.


## تقدم T01

- [x] CI matrix: Python 3.12/3.13/3.14.
- [x] ترقية `python-telegram-bot` إلى stable 22.8.
- [x] إبقاء `httpx` على stable 0.28.1 وعدم اعتماد 1.0 prerelease.
- [x] ترقية `aiosqlite` إلى stable 0.22.1.
- [x] إضافة `requirements.in` و`constraints.txt`.
- [x] إضافة full stable runtime lock في `requirements.lock`.
- [x] إضافة Dependabot لـpip/npm/GitHub Actions.
- [x] إضافة upgrade/runtime dependency tests.
- [x] تشغيل `pip check` على كل Python matrix.
- [x] جعل `DeprecationWarning` خطأ في unit tests.
- [x] توثيق قرارات runtime/dependencies.
- [x] نجاح كامل CI على commit التوثيق النهائي.

## أدلة T01

### Runtime / dependencies
- `python-telegram-bot==22.8`
- `httpx==0.28.1`
- `aiosqlite==0.22.1`
- `requirements.in`: compatibility intent.
- `requirements.txt`: direct exact runtime pins.
- `constraints.txt`: stable-line guardrails.
- `requirements.lock`: full pinned runtime closure لـPython 3.12–3.14.

### CI / policy
- Python matrix: 3.12, 3.13, 3.14.
- `PYTHONWARNINGS=error::DeprecationWarning`.
- `python -m pip check`.
- `.github/dependabot.yml` يغطي pip/npm/github-actions.
- `tests/test_dependency_policy.py` يثبت pins/lock/matrix/Dependabot والنسخ المثبتة فعليًا.

### Documentation
- `docs/t01-runtime-dependencies-ar.md`

### Commit / CI
- commit التوثيق النهائي لـT01: `8d98b7e3bb8db8f28da4e7585a536eeb7a24c521`
- GitHub Actions: https://github.com/Alaa91H/opencode-bridge/actions/runs/36354198319
- النتيجة: **success** على Python 3.12/3.13/3.14.
- كل job نجح في: locked install, pip check, syntax, unit tests, baseline inventory, micro-benchmark, shell validation, OpenCode configs, version validation.

### ملاحظة supply-chain
- T01 يقفل الإصدارات ويمنع prerelease.
- hash locking/SBOM/attestation ليست ناقصة من T01؛ هي ضمن **T38** المخصصة لسلسلة التوريد، ولذلك لم يتم تقديمها خارج ترتيب الخطة.

## سجل التنفيذ — T01

### 2026-09-27
1. تم التحقق من الإصدارات المستقرة من المصادر الرسمية.
2. تمت ترقية PTB 21.6 → 22.8 وaiosqlite 0.20.0 → 0.22.1.
3. تم إبقاء HTTPX 0.28.1 لأن خط 1.0 الحالي prerelease.
4. تمت إضافة stable compatibility ranges.
5. تمت إضافة full runtime version lock.
6. تمت إضافة Python 3.12/3.13/3.14 matrix.
7. تمت إضافة warnings-as-errors وpip check.
8. تمت إضافة Dependabot.
9. تمت إضافة اختبارات dependency policy والنسخ المثبتة فعليًا.
10. نجح CI النهائي على المصفوفة الثلاثية.
11. **T01 مغلقة.**

## الخطوة التالية المسموحة فقط

بدء **T02 — تفكيك bot.py وإعادة بناء Architecture** بقراءة ملف الخطة كاملًا أولًا ثم ملف الحالة هذا. يجب الحفاظ على production compatibility أثناء النقل، ولا يجوز حذف المسارات القديمة قبل تغطية behavior بعقود واختبارات. لا يبدأ T03 قبل إغلاق T02 بالكامل.

## تقدم T02

- [x] Telegram handlers لا تحتوي business logic؛ كل compatibility handlers أصبحت delegates رفيعة ومفروضة باختبار AST.
- [x] SQL لا يظهر في Telegram command adapters.
- [x] OpenCode layer لا تعتمد على Telegram.
- [x] scheduler مستقل عن bot عبر `ScheduleService`.
- [x] attachment pipeline مستقلة وقابلة للاختبار عبر `MediaTaskService`.
- [x] compatibility layer مؤقتة تمنع كسر الإنتاج أثناء النقل.
- [x] تغطية اختبارات قبل حذف أي مسار legacy.
- [x] إنشاء كامل حدود الهيكل المستهدف: telegram/domain/services/infrastructure/workers/config.
- [x] نقل دورة تنفيذ queue من `bot.py` إلى `TaskExecutionService`.
- [x] نقل OpenCode variant/file/model fallback إلى `AgentService`.
- [x] نقل workspace/V3 وCI/resource/maintenance business rules خلف services/adapters مستقلة.
- [x] توثيق Architecture T02.
- [x] نجاح كامل CI على Python 3.12/3.13/3.14.

## أدلة T02

### Architecture
- `docs/t02-architecture-ar.md`
- `bridge/telegram/app.py`
- `bridge/telegram/middleware.py`
- `bridge/telegram/execution.py`
- `bridge/telegram/commands/*`
- `bridge/services/task_execution_service.py`
- `bridge/services/task_service.py`
- `bridge/services/schedule_service.py`
- `bridge/services/media_service.py`
- `bridge/services/agent_service.py`
- `bridge/services/workspace_service.py`
- `bridge/services/ci_service.py`
- `bridge/services/resource_service.py`
- `bridge/services/maintenance_service.py`
- `bridge/services/notification_service.py`

### Compatibility
- `bot.py` بقي production compatibility surface مع thin handlers وthin task-execution wrapper.
- `run_v3.py` production entrypoint لم يتغير.
- `v3_plugin.py`, `ci_plugin.py`, `resource_commands.py` بقيت install/compatibility layers فقط.
- حذف legacy/V3 duplication الكامل لم يتم تقديمه خارج ترتيبه؛ يبقى ضمن T46.

### Tests
- `tests/test_v2_architecture_boundaries.py`
- `tests/test_v2_task_architecture.py`
- `tests/test_v2_media_architecture.py`
- `tests/test_v2_task_execution_service.py`
- `tests/test_v2_workspace_service.py`
- `tests/test_v2_agent_service.py`
- `tests/test_bot_import.py`
- `tests/test_v2_baseline_contract.py`

### CI
- commit آخر تنفيذ/اختبارات T02 قبل تحديث سجل الحالة: `d770c8028e130b34815d5a6bbc5517d01d332140`
- GitHub Actions: https://github.com/Alaa91H/opencode-bridge/actions/runs/36358325811
- النتيجة: **success**
- نجح CI على Python 3.12 و3.13 و3.14، بما في ذلك locked dependency install، `pip check`، syntax، unit tests، baseline inventory/benchmark، shell validation، OpenCode config validation، version validation.

## سجل التنفيذ — T02

### 2026-09-27 / 2026-09-28
1. تم إنشاء `bridge/telegram`, `bridge/domain`, `bridge/services` ثم استكمال boundaries الخاصة بـinfrastructure/workers/config.
2. تم نقل schedule rules من Telegram handlers إلى `ScheduleService`.
3. تم نقل task enqueue/cancel/progress routing إلى `TaskApplicationService`.
4. تم نقل pending attachments/media-group orchestration خلف `MediaTaskService` و`TelegramMediaAdapter`.
5. تم نقل Agent/session/model logic إلى `AgentService`.
6. تم نقل workspace business rules من `v3_plugin.py` إلى `WorkspaceService`.
7. تم تحويل CI/resource/system plugins إلى thin Telegram adapters.
8. تم استخراج Telegram access middleware وapplication registration.
9. تم نقل دورة تنفيذ المهمة كاملة إلى `TaskExecutionService` مع `TelegramExecutionDelivery`.
10. تم نقل variant/file/model fallback إلى `AgentService` مع اختبارات مباشرة.
11. تم فرض اختبارات تمنع SQL أو Telegram imports عبر الحدود غير المسموحة وتمنع handlers غير الرفيعة.
12. تم الحفاظ على compatibility وعدم حذف production paths القديمة.
13. نجح كامل CI على commit `d770c8028e130b34815d5a6bbc5517d01d332140`.
14. **T02 مغلقة.**

## الخطوة التالية المسموحة فقط

بدء **T03 — نظام Configuration مركزي** فقط:
1. إنشاء `BridgeSettings` typed ومتحقق منه عند التشغيل.
2. دعم env + config file + defaults + per-user policy + per-task override + feature flags.
3. إزالة قراءات `os.environ` المتفرقة ضمن نطاق T03.
4. إضافة `/config` لعرض الإعدادات غير السرية.
5. إضافة `/limits` لعرض الحدود الفعلية.
6. إضافة validation tests للأخطاء والتعارضات.
7. عدم بدء T04 قبل إغلاق T03 بالكامل ونجاح CI.



## تقدم T03 — مكتملة

- [x] BridgeSettings typed والتحقق عند التشغيل.
- [x] env + config file + defaults + per-user policy + per-task override + feature flags.
- [x] توحيد قراءات إعدادات Python التشغيلية عبر get_settings مع توافق الإعدادات القديمة.
- [x] /config لعرض الإعدادات غير السرية.
- [x] /limits لعرض الحدود الفعلية.
- [x] validation وprecedence tests في tests/test_v3_settings.py.
- [x] توثيق BRIDGE_CONFIG_FILE في .env.example.
- [x] نجاح CI النهائي وتسجيل رابط التشغيل.

### أدلة T03
- bridge/config/settings.py
- bridge/services/config_service.py
- bridge/telegram/commands/config.py
- bridge/telegram/rendering/config.py
- tests/test_v3_settings.py
- commit الاختبارات: f0818c4ad6af2ccc7328ad0edaf94d1521767969
- commit bot المركزي: e87fd72ae04db809daed4fa95874be49dff68f64
- commit تسجيل الأوامر: 1a433d2b2d597e260a9189e7e60ec7556fc48bbb
- commit help: e50b7edcdfa0ee136ef998ff71249228ff038813
- commit توثيق BRIDGE_CONFIG_FILE: 04bb94fa9ccf50340b438e15a9d81d16e340b700

### CI النهائي وإغلاق T03
- commit المتحقق منه: `0df11ec2f8f0d3e696508dc3776593cd4b58faf7`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36360198610
- النتيجة: **success**
- نجحت jobs: Python 3.12 وPython 3.13 وPython 3.14، وكل خطوات locked install وdependency graph وsyntax وunit tests وbaseline probe/benchmark وshell/OpenCode/version validation.
- **T03 مغلقة.**

## الخطوة التالية غير المكتملة بالضبط

T04 — Database Layer v2: **توحيد SessionStore وTaskQueueStore خلف طبقة DB واحدة**. لا يبدأ أي جزء من T05 قبل استكمال جميع بنود T04 واختباراتها وخطة rollback ونجاح CI.


## تقدم T04 — مكتملة

- [x] توحيد SessionStore وTaskQueueStore خلف BridgeDatabase واحدة مع دعم حقن connection lifecycle مشترك.
- [x] WAL وbusy_timeout وforeign_keys وsynchronous policy مركزية.
- [x] transaction boundaries مع BEGIN/BEGIN IMMEDIATE وcommit/rollback.
- [x] indexes مثبتة باختبارات EXPLAIN QUERY PLAN للمسارات الحرجة.
- [x] checkpoint management: PASSIVE/FULL/RESTART/TRUNCATE.
- [x] periodic integrity check بفاصل افتراضي 24 ساعة.
- [x] schema_migrations مع version/name/checksum/applied_at.
- [x] migrations صريحة بدل ALTER المبعثر داخل TaskQueueStore.init.
- [x] الجداول المستقلة المطلوبة: task_attempts, task_events, attachments, task_outputs, schedules, schedule_runs, agent_sessions, user_settings, resource_snapshots, failure_records.
- [x] migration/idempotency/transaction rollback tests وخطة rollback موثقة.

### أدلة T04
- `bridge/infrastructure/database/sqlite.py`
- `bridge/infrastructure/database/migrations.py`
- `session_store.py`, `task_queue.py`, `workspace_store.py`
- `tests/test_v2_database_layer.py`
- `tests/test_v2_database_migrations.py`
- `tests/test_v2_database_query_plans.py`
- `docs/t04-database-layer-ar.md`
- إصلاح regression لعقد baseline بعد نقل schema إلى migrations: commit `2975c7df853f89168c46333479b852394a822397`.

### CI النهائي لـT04
- commit المتحقق منه: `2975c7df853f89168c46333479b852394a822397`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36384440768
- النتيجة: **success**
- نجحت jobs: Python 3.12 وPython 3.13 وPython 3.14.
- **T04 مغلقة.**

## الخطوة التالية غير المكتملة بالضبط

T05 — Durable Queue v2: إضافة UUID عام وidempotency key وlease وheartbeat وattempt/priority/checkpoint والحالات الجديدة، ثم recovery/retry/DLQ وأوامر /failed و/retry واختبارات crash/restart. لا يبدأ T06 قبل إغلاق T05 بالكامل ونجاح CI.


## تقدم T05 — مكتملة

- [x] UUID عام وidempotency key وlease وheartbeat وattempt/priority/checkpoint لكل Task.
- [x] الحالات المطلوبة مدعومة في durable state model مع توافق legacy أثناء الترحيل.
- [x] worker lease واسترجاع المهمة بعد موت worker/انتهاء lease.
- [x] heartbeat وتمديد lease للworker المالك فقط.
- [x] retry engine مع exponential backoff + jitter واحترام Retry-After.
- [x] تصنيف retryable/non-retryable.
- [x] Dead Letter Queue.
- [x] /failed و/retry عبر Telegram → service → durable store مع owner isolation.
- [x] crash/restart-style tests: lease expiry/reclaim، heartbeat، attempts، retry، DLQ، manual retry، idempotency uniqueness.
- [x] توثيق state/recovery/failure semantics.

### أدلة T05
- `bridge/infrastructure/database/migrations.py` — migration durable_queue_v2.
- `task_queue.py` — durable metadata، claim/lease/heartbeat/recovery/retry/DLQ.
- `bridge/services/task_service.py` — failed/retry use cases.
- `bridge/telegram/commands/tasks.py`, `bridge/telegram/app.py`, `bot.py` — /failed و/retry مع thin compatibility handlers.
- `tests/test_v2_durable_queue.py`.
- `docs/t05-durable-queue-ar.md`.
- commits الرئيسية: `32f0edc45b04b80d64eda77016ced11e27eef257`, `a41f4cbbbbabe05f08b29f10ad8c0e6b01f295c6`, `40c1755c63404bd058be0f8bf3e19ab1ac4f1f32`, `c862460e92d5f3e46c3124862fd9bd5ecec4550b`, `9cf36ec90ce29d6aec6a5f55d246ce12c55d9310`.

### CI النهائي لـT05
- commit المتحقق منه: `9cf36ec90ce29d6aec6a5f55d246ce12c55d9310`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36384731641
- النتيجة: **success**
- نجحت jobs: Python 3.12 وPython 3.13 وPython 3.14.
- **T05 مغلقة.**

## الخطوة التالية غير المكتملة بالضبط

T06 — Idempotency ومنع التكرار: idempotency record لكل Telegram update، at-least-once input مع effectively-once task creation، منع duplicate schedule occurrence وduplicate enqueue بعد restart، واختبارات update/tick مكررة. لا يبدأ T07 قبل إغلاق T06 بالكامل ونجاح CI.


## تقدم T06 — مكتملة

- [x] idempotency record دائم لكل Telegram update عبر scope + update_id.
- [x] at-least-once input مع effectively-once task creation بمعاملة SQLite ذرية.
- [x] منع duplicate schedule occurrence عبر primary key مركب وINSERT OR IGNORE.
- [x] منع duplicate enqueue بعد restart.
- [x] اختبارات Telegram update مكرر وschedule tick مكرر قبل/بعد restart.
- [x] إصلاح lock re-entry في مسار duplicate delivery.
- [x] توثيق semantics والatomicity وحدود المرحلة.

### أدلة T06
- `bridge/infrastructure/database/migrations.py` — idempotency_records وschedule_occurrences.
- `task_queue.py` — enqueue_once وclaim_schedule_occurrence.
- `bridge/services/task_service.py` — تمرير idempotency إلى repository.
- `bridge/telegram/commands/tasks.py` — update.update_id كمفتاح Telegram.
- `tests/test_v2_idempotency.py`.
- `docs/t06-idempotency-ar.md`.
- commits: `e493391e84c0a43a958c99ff236eec8d1b579191`, `f724688ddbac641497f92b6c39cac400b14c4ecf`, `229ae8adea092b91a4eec4485d1b88eff0835cfb`, `6b653a779dedf73d7e9472d295b0db99725ddd29`, `227a374412b6d1dcf685e512090c3dc69e2b6357`, `75f75a468057c5b898c22b334c987b729cfc8e63`.

### CI النهائي لـT06
- commit المتحقق منه: `75f75a468057c5b898c22b334c987b729cfc8e63`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36385053502
- النتيجة: **success**
- نجحت jobs: Python 3.12 وPython 3.13 وPython 3.14.
- **T06 مغلقة.**

## الخطوة التالية غير المكتملة بالضبط

T07 — Scheduler Engine v2: once/interval/cron/daily/weekly/monthly/weekdays مع IANA timezone وDST، misfire/overlap policies، history و/schedhistory وrecovery بعد downtime واختبارات DST. لا يبدأ T08 قبل إغلاق T07 بالكامل ونجاح CI.


## تقدم T07 — مكتملة

- [x] once/interval/cron/daily/weekly/monthly/weekdays.
- [x] user timezone عبر IANA zoneinfo مع validation.
- [x] DST-aware wall-clock recurrence واختبارات spring-forward/fall-back.
- [x] misfire policies: skip/run_once/catch_up/coalesce.
- [x] overlap policies: forbid/allow/replace/queue مع قرارات domain واختبارات.
- [x] schedule history durable.
- [x] /schedhistory عبر service/Telegram adapter رفيع.
- [x] recovery بعد downtime بمعاملة idempotent ومنع duplicate occurrence.
- [x] توافق legacy scheduled_jobs مع catalog v2 دون كسر FK.
- [x] توثيق Scheduler v2.

### أدلة T07
- `bridge/domain/schedules/engine.py`.
- `bridge/infrastructure/database/migrations.py` — scheduler_v2 migration.
- `task_queue.py` — recovery/history/materialization.
- `bridge/services/schedule_service.py`.
- `bridge/telegram/commands/schedules.py`, `bridge/telegram/app.py`, `bot.py`.
- `tests/test_v2_scheduler_engine.py`, `tests/test_v2_scheduler_recovery.py`.
- `docs/t07-scheduler-v2-ar.md`.
- commits الرئيسية: `e535aa2a`, `0bb2719e`, `40a617ee`, `6991d99d`, `6b392504`, `e1f6cdee`, `94e923a3`, `7abc864c`.

### CI النهائي لـT07
- commit المتحقق منه: `7abc864c747c9949bf88f175e9665c3b6b87ddec`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36385545521
- النتيجة: **success**
- نجحت jobs على Python 3.12 وPython 3.13 وPython 3.14.
- **T07 مغلقة.**

## الخطوة التالية غير المكتملة بالضبط

T08 — واجهة إدارة الجدولة الحديثة: Inline Keyboard، pagination، تشغيل/إيقاف/استئناف، تعديل الاسم/الأمر/الوقت/timezone/recurrence، history، duplicate، delete confirmation. لا يبدأ T09 قبل إغلاق T08 بالكامل ونجاح CI.


## تقدم T08 — مكتملة

- [x] Inline Keyboard لمتصفح الجدولات.
- [x] pagination ديناميكية.
- [x] تشغيل الآن/إيقاف/استئناف.
- [x] تعديل الاسم/الأمر/الوقت/timezone/recurrence مع بقاء الأوامر النصية متوافقة.
- [x] history.
- [x] duplicate باسم غير متعارض.
- [x] delete confirmation بخطوتين.
- [x] owner isolation لكل callbacks.
- [x] اختبارات browser/actions/confirmation.
- [x] توثيق واجهة الإدارة الحديثة.

### أدلة T08
- `bridge/telegram/rendering/schedule_browser.py`.
- `bridge/telegram/callbacks/schedules.py`.
- `bridge/telegram/commands/schedules.py`.
- `bridge/services/schedule_service.py`.
- `bridge/telegram/app.py`, `bot.py`.
- `tests/test_v2_schedule_ui.py`.
- `docs/t08-schedule-ui-ar.md`.

### CI النهائي لـT08
- commit المتحقق منه: `1767d350fb70bf6ef7b3071f08d6cab9f5cc16cb`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36385791260
- النتيجة: **success**
- نجحت مصفوفة Python 3.12/3.13/3.14.
- **T08 مغلقة.**

## الخطوة التالية غير المكتملة بالضبط

T09 — Draft / Prompt Editor للأوامر الضخمة: /draft new، تجميع رسائل وملفات في Draft دائم، show/clear/save/run/schedule، prompt versioning، دعم أحجام كبيرة حسب storage policy، وrestart persistence. لا يبدأ T10 قبل إغلاق T09 بالكامل ونجاح CI.


## تقدم T09 — مكتملة

- [x] `/draft new` وإنشاء Draft دائم versioned.
- [x] جمع عدة رسائل وملفات عبر DraftIntakeAdapter مع بقاء compatibility handlers رفيعة.
- [x] `/draft show`, `clear`, `save`, `run`, `schedule`.
- [x] prompt versioning في `draft_versions` مع snapshot لكل تعديل.
- [x] اختبار prompt بحجم 1 MiB دون قص اصطناعي.
- [x] restart persistence بإغلاق BridgeDatabase وإعادة فتحها ثم استعادة المحتوى والإصدار.
- [x] owner-scoped DraftStore وعدم تخزين binary داخل SQLite.
- [x] توثيق T09.

### أدلة T09
- `bridge/infrastructure/database/draft_store.py`
- `bridge/services/draft_service.py`
- `bridge/telegram/commands/drafts.py`
- `bridge/telegram/draft_intake.py`
- `tests/test_v2_drafts.py`
- `docs/t09-draft-editor-ar.md`
- commit بوابة الاختبارات المعمارية: `5dcac7af3f2992c559a693ef1de0b53b0a213ed1`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36386713637

### CI النهائي وإغلاق T09
- commit المتحقق منه: `59305ddc111b283e7fdfaf230f1268b59d4a6ef0`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36387350322
- النتيجة: **success**
- نجحت jobs على Python 3.12 وPython 3.13 وPython 3.14.
- **T09 مغلقة.**

## الخطوة التالية غير المكتملة بالضبط

T10 — إزالة قيود Telegram للملفات الكبيرة: Local Bot API اختياري، `TELEGRAM_API_MODE=cloud|local`، local paths في local mode، streaming download/upload، capability detection، وعدم تحميل الملف كاملًا إلى RAM. لا يبدأ T11 قبل إغلاق T10 بالكامل ونجاح CI.


## تقدم T10 — مكتملة

- [x] دعم اختياري Telegram Local Bot API Server مع بقاء cloud هو الافتراضي.
- [x] `TELEGRAM_API_MODE=cloud|local` typed ومتحقق منه.
- [x] دعم local file paths في local mode عبر streamed disk copy.
- [x] streaming download عبر `download_to_drive` في cloud وchunked copy في local.
- [x] streaming upload بتمرير file handle إلى `send_document` دون bytes buffer.
- [x] capability detection مستقل عبر `TelegramCapabilities`.
- [x] لا يوجد مسار T10 يحمل الملف كاملًا إلى RAM.
- [x] اختبارات cloud/local/validation/download/upload.
- [x] توثيق T10.

### أدلة T10
- `bridge/config/settings.py`
- `bridge/infrastructure/telegram/capabilities.py`
- `bridge/telegram/app.py`
- `attachments.py`
- `bridge/telegram/execution.py`
- `tests/test_v2_telegram_transport.py`
- `.env.example`
- `docs/t10-telegram-large-files-ar.md`
- commits التنفيذية: `6d0672d2`, `fa17a4b2`, `4289dfc3`, `41a427de`, `99afc1d6`, `ba268519`, `3f373e08`, `5da7ef64`.

### CI
- اختبارات النقل الجديدة: https://github.com/Alaa91H/opencode-bridge/actions/runs/36387662978 — success.
- آخر CI مكتمل قبل وثيقة T10: https://github.com/Alaa91H/opencode-bridge/actions/runs/36387669238 — success.
- CI النهائي لوثيقة T10: https://github.com/Alaa91H/opencode-bridge/actions/runs/36387770136 — **success** على commit `5da7ef64c11c722d67af62ad381c69bf3c774428` ومصفوفة Python 3.12/3.13/3.14.\n- **T10 مغلقة تنفيذيًا.**

## الخطوة التالية المشروطة

بعد نجاح CI للرأس الحالي فقط: T11 — Attachment Storage v2. لا يبدأ T11 قبل ذلك.


## تقدم T11 — Attachment Storage v2

- [x] StorageBackend abstraction.
- [x] LocalStorage.
- [x] S3/MinIO optional عبر S3-compatible client دون dependency إلزامية.
- [x] content-addressed storage حسب SHA-256.
- [x] deduplication للـblobs المتطابقة.
- [x] reference counting دائم ومعاملاتي.
- [x] metadata: file_id/file_unique_id/hash/detected MIME/claimed MIME/size/owner/retention/scan state.
- [x] cleanup policies للـexpired references والـorphan blobs.
- [x] owner isolation عند release.
- [x] اختبارات Local CAS وmetadata وretention وS3 contract.
- [x] توثيق T11.

### أدلة T11
- `bridge/infrastructure/storage/backend.py`
- `bridge/infrastructure/storage/local.py`
- `bridge/infrastructure/storage/s3.py`
- `bridge/infrastructure/database/migrations.py` — migration 7.
- `bridge/infrastructure/database/attachment_store.py`
- `bridge/services/attachment_storage_service.py`
- `tests/test_v2_attachment_storage.py`
- `tests/test_v2_s3_storage.py`
- `docs/t11-attachment-storage-v2-ar.md`
- CI Local CAS/metadata: https://github.com/Alaa91H/opencode-bridge/actions/runs/36387962628 — success.

### بوابة إغلاق T11
لا تعتبر T11 مغلقة تنفيذيًا حتى ينجح CI للرأس الذي يحتوي S3 contract + توثيق T11 + سجل الحالة على Python 3.12/3.13/3.14. لا يبدأ T12 قبل ذلك.
