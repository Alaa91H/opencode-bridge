# OpenCode Bridge 2.0 — خطة التنفيذ الملزمة

> **قاعدة التنفيذ الصارمة:** هذا الملف هو المرجع التنفيذي الملزم. في كل تشغيل يجب قراءته كاملًا قبل أي تعديل، ثم قراءة `docs/opencode-bridge-2.0-execution-state.md`. التنفيذ تسلسلي فقط: T00 ثم T01 ثم T02 … حتى T50. ممنوع بدء أي جزء من المرحلة التالية قبل إغلاق المرحلة الحالية بالكامل، بما في ذلك جميع البنود الفرعية والاختبارات ومعايير القبول والتوثيق. لا تعتمد على الذاكرة أو ملخصات سابقة بدل هذا الملف.

## الهدف النهائي

تحويل `opencode-bridge` إلى منصة مهام وكيلة دائمة وعالية الاعتمادية تستطيع استقبال النصوص والصور والفيديو والصوت والملفات الكبيرة، تنفيذ المهام الفورية والمجدولة والمتكررة، تحمل إعادة التشغيل دون فقدان العمل، دعم أوامر ضخمة جدًا، العمل على عدة مستودعات ومساحات عمل معزولة، اختيار النموذج الأنسب تلقائيًا، واستغلال موارد الخادم بكفاءة عالية مع إزالة القيود الاصطناعية وتحويل القيود الضرورية إلى سياسات ديناميكية وآمنة.

## المبدأ الحاكم: إزالة القيود بدون إزالة الأمان

### قيود داخلية يجب التخلص منها أو جعلها ديناميكية

- حدود عدد المرفقات والحجم الإجمالي ومدة انتظار الأمر.
- أقل تكرار ثابت للجدولة.
- عدد workers ثابت.
- عدد ملفات ناتجة ثابت.
- حدود قوائم ثابتة.
- UTC الإجباري.
- افتراض نموذج واحد لمعظم المهام.
- قص الرد النهائي بسبب حد Telegram.
- افتراض أن المهمة لا يمكن تقسيمها إلى مراحل.

يجب تحويل هذه القيود إلى **Policy/Resource Budget** قابل للضبط، مع دعم `0 = unlimited by application policy` عندما يكون ذلك آمنًا.

### قيود خارجية لا يمكن إلغاؤها مباشرة

- حدود Telegram.
- حدود context الخاصة بالنماذج.
- حدود OpenCode للمرفق المباشر.
- RAM/CPU/disk الفعلية.
- rate limits لموفري النماذج.

تُعالج عبر streaming والتقسيم والتخزين المحلي وLocal Bot API وfallback وsummarization وbatching.

### قيود أمنية لا تُلغى

- منع قراءة/تسريب الأسرار مثل `.env` والمفاتيح الخاصة.
- منع force-push والحذف المدمر غير المصرح.
- عدم تنفيذ ملف مرفق مجهول أو macro/binary وارد.
- عدم تثبيت برامج عشوائية على host الإنتاج.

تُستبدل القيود العامة عند الحاجة بـ execution profiles وبيئات معزولة.

---

# T00 — تثبيت Baseline للإصدار 2.0

- [ ] إنشاء وثيقة Architecture Baseline للحالة الحالية.
- [ ] تسجيل جميع جداول SQLite الحالية ومخططها.
- [ ] تسجيل جميع متغيرات البيئة الحالية.
- [ ] حصر أوامر Telegram الحالية.
- [ ] حصر جميع خدمات systemd والمؤقتات.
- [ ] تسجيل حدود المرفقات الحالية.
- [ ] تسجيل سياسات OpenCode الحالية.
- [ ] إنشاء اختبارات regression للوظائف الأساسية قبل refactor.
- [ ] إنشاء benchmark baseline للذاكرة والـCPU وقاعدة البيانات.
- [ ] توثيق نتائج baseline وCI على commit إغلاق T00.

**Acceptance T00:** لا تبدأ T01 حتى تكون كل البنود أعلاه مكتملة ومثبتة في المستودع ويكون CI ناجحًا.

# T01 — تحديث Runtime والاعتماديات

- [ ] CI matrix: Python 3.12/3.13/3.14.
- [ ] ترقية `python-telegram-bot` إلى أحدث stable متوافق.
- [ ] إبقاء `httpx` على stable فقط، وعدم استخدام prerelease تلقائيًا.
- [ ] تحديث `aiosqlite` إلى آخر stable متوافق.
- [ ] إضافة constraints/lock reproducible.
- [ ] Dependabot أو Renovate.
- [ ] اختبارات upgrade آلية.
- [ ] إزالة deprecation warnings الحرجة.

**Acceptance T01:** كامل CI ينجح على المصفوفة الجديدة ولا تعتمد بيئة الإنتاج على prerelease.

# T02 — تفكيك bot.py وإعادة بناء Architecture

الهيكل المستهدف:

```text
bridge/
  telegram/
    app.py
    middleware.py
    commands/
    callbacks/
    rendering/
  domain/
    tasks/
    schedules/
    attachments/
    sessions/
    policies/
  services/
    task_service.py
    schedule_service.py
    media_service.py
    agent_service.py
    notification_service.py
  infrastructure/
    database/
    opencode/
    telegram/
    storage/
    metrics/
  workers/
  config/
```

- [ ] Telegram handlers لا تحتوي business logic.
- [ ] SQL لا يظهر في handlers.
- [ ] OpenCode layer لا تعتمد على Telegram.
- [ ] scheduler مستقل عن bot.
- [ ] attachment pipeline مستقلة وقابلة للاختبار.
- [ ] compatibility layer مؤقتة تمنع كسر الإنتاج أثناء النقل.
- [ ] تغطية اختبارات قبل حذف المسارات القديمة.

# T03 — نظام Configuration مركزي

- [ ] إنشاء `BridgeSettings` typed ومتحقق منه عند التشغيل.
- [ ] دعم env + config file + defaults + per-user policy + per-task override + feature flags.
- [ ] إزالة قراءات `os.environ` المتفرقة.
- [ ] `/config` لعرض الإعدادات غير السرية.
- [ ] `/limits` لعرض الحدود الفعلية في اللحظة الحالية.
- [ ] اختبار validation للأخطاء والتعارضات.

# T04 — Database Layer v2

- [ ] توحيد SessionStore وTaskQueueStore خلف طبقة DB واحدة.
- [ ] تقييم/تفعيل WAL وbusy timeout وforeign keys.
- [ ] transaction boundaries صحيحة.
- [ ] indexes مبنية على query plan.
- [ ] checkpoint management.
- [ ] integrity check دوري.
- [ ] جدول `schema_migrations`.
- [ ] migrations صريحة بدل ALTER مبعثر في init.
- [ ] إنشاء جداول مستقلة: task_attempts, task_events, attachments, task_outputs, schedules, schedule_runs, agent_sessions, user_settings, resource_snapshots, failure_records.
- [ ] migration tests وrollback plan.

# T05 — Durable Queue v2

كل Task يحصل على UUID عام وidempotency key وlease وheartbeat وattempt/priority/checkpoint.

الحالات:
`draft, queued, leased, running, waiting, retrying, completed, failed, cancelled, dead_letter`.

- [ ] worker lease واسترجاع المهمة بعد موت worker.
- [ ] heartbeat.
- [ ] retry engine مع exponential backoff + jitter + Retry-After.
- [ ] تصنيف retryable/non-retryable.
- [ ] Dead Letter Queue.
- [ ] `/failed` و`/retry`.
- [ ] crash/restart tests.

# T06 — Idempotency ومنع التكرار

- [ ] idempotency record لكل Telegram update.
- [ ] at-least-once input + effectively-once task creation.
- [ ] منع duplicate schedule occurrence.
- [ ] منع duplicate enqueue بعد restart.
- [ ] اختبارات تحديث Telegram مكرر وschedule tick مكرر.

# T07 — Scheduler Engine v2

يدعم once/interval/cron/daily/weekly/monthly/weekdays/timezone/DST.

- [ ] user timezone IANA.
- [ ] misfire policies: skip/run_once/catch_up/coalesce.
- [ ] overlap policies: forbid/allow/replace/queue.
- [ ] schedule history.
- [ ] `/schedhistory`.
- [ ] recovery بعد downtime.
- [ ] DST tests.

# T08 — واجهة إدارة الجدولة الحديثة

- [ ] Inline Keyboard.
- [ ] pagination.
- [ ] تشغيل/إيقاف/استئناف.
- [ ] تعديل الاسم/الأمر/الوقت/timezone/recurrence.
- [ ] history.
- [ ] duplicate.
- [ ] delete confirmation.

# T09 — Draft / Prompt Editor للأوامر الضخمة

- [ ] `/draft new`.
- [ ] جمع عدة رسائل وملفات في Draft دائم.
- [ ] `/draft show`, `/draft clear`, `/draft save`, `/draft run`, `/draft schedule`.
- [ ] prompt versioning.
- [ ] دعم مئات KB/MB حسب storage policy.
- [ ] restart persistence.

# T10 — إزالة قيود Telegram للملفات الكبيرة

- [ ] دعم اختياري Telegram Local Bot API Server.
- [ ] `TELEGRAM_API_MODE=cloud|local`.
- [ ] local file paths في local mode.
- [ ] streaming download/upload.
- [ ] capability detection.
- [ ] لا تحميل كامل الملف إلى RAM.

# T11 — Attachment Storage v2

- [ ] StorageBackend abstraction.
- [ ] LocalStorage.
- [ ] S3/MinIO optional.
- [ ] content-addressed storage حسب SHA-256.
- [ ] deduplication.
- [ ] reference counting.
- [ ] metadata: file_id/file_unique_id/hash/detected MIME/claimed MIME/size/owner/retention/scan state.
- [ ] cleanup policies.

# T12 — Streaming وليس Buffering

- [ ] download/upload/hash/copy/archive/media على chunks.
- [ ] قياس RAM مع ملفات كبيرة.
- [ ] backpressure.
- [ ] cancellation أثناء stream.
- [ ] اختبارات ملفات متعددة GB عبر mocks/local mode دون RAM proportional growth.

# T13 — Media Processing Pipeline

Pipeline: input → detect → validate → normalize → extract → segment → agent → compose.

- [ ] الصور: metadata/normalize/tiling/OCR اختياري/multi-image.
- [ ] PDF: text/pages/images/tables/chunking.
- [ ] Video: ffprobe/audio/keyframes/scene detection/transcript/frames.
- [ ] Audio: normalize/segment/STT/timestamps/speaker segmentation عند توفره.
- [ ] Archives: list أولًا، safe extraction، path traversal، decompression-bomb limits.
- [ ] tool availability detection.
- [ ] لا dependency installation عشوائي.

# T14 — تجاوز حد المرفق المباشر لـ OpenCode

Attachment Intelligence Router:

- [ ] small supported → direct attachment.
- [ ] large text → chunk/index/select.
- [ ] PDF → text/pages/images.
- [ ] video → frames + transcript.
- [ ] audio → transcript.
- [ ] archive → manifest + selected files.
- [ ] context-aware selection.

# T15 — OpenCode Client v2

- [ ] typed structured models.
- [ ] endpoint contracts.
- [ ] centralized HTTP config.
- [ ] retry middleware.
- [ ] timeout policies.
- [ ] request/correlation IDs.
- [ ] metrics.
- [ ] streaming event reconnection.
- [ ] contract tests.
- [ ] تقييم توليد client من OpenAPI إن توفر رسميًا.

# T16 — Circuit Breakers

Circuit breaker مستقل لـ Telegram/OpenCode/model provider/GitHub/storage.

- [ ] CLOSED/OPEN/HALF_OPEN.
- [ ] thresholds قابلة للضبط.
- [ ] metrics/events.
- [ ] recovery tests.

# T17 — Model Router v2

- [ ] routing حسب text/image/coding/long context/reasoning/latency/cost/availability/quota/failure history.
- [ ] dynamic scoring: capability/quality/latency/reliability/cost/context.
- [ ] fallback chain.
- [ ] per-task model choice بدون تلويث global preference.
- [ ] اختبارات catalog تغيره أثناء التشغيل.

# T18 — Context Management

- [ ] context budget.
- [ ] conversation summarization.
- [ ] retrieval من تاريخ المهمة.
- [ ] selective attachment retrieval.
- [ ] token estimation.
- [ ] prompt compression.
- [ ] context checkpoints.
- [ ] اختبارات تجاوز context دون فقد المهمة.

# T19 — Execution Policy Profiles

Profiles: SAFE / DEVELOPMENT / POWER / HOST_ADMIN.

- [ ] capability matrix.
- [ ] per-user/per-task selection.
- [ ] audit لكل رفع صلاحيات.
- [ ] default SAFE.
- [ ] HOST_ADMIN محكوم allowlist.
- [ ] لا كشف أسرار.

# T20 — Build Sandbox

- [ ] اختيار sandbox موثوق: rootless Podman/systemd transient/bubblewrap حسب البيئة.
- [ ] CPU/RAM/disk/wall-time/process/network limits.
- [ ] السماح بالبناء وتثبيت dependencies داخل sandbox فقط.
- [ ] npm/Gradle/Python/build tests داخل sandbox.
- [ ] cleanup.
- [ ] breakout/security tests.

# T21 — Workspace Isolation

- [ ] workspace مستقل لكل مهمة تطوير.
- [ ] Git worktree/temp clone/container volume.
- [ ] منع مهمتين من تعديل نفس working tree.
- [ ] commit/push/PR وفق policy.
- [ ] retention للتشخيص عند الفشل.
- [ ] cleanup آمن.

# T22 — Resource Scheduler v2

- [ ] Resource Cost لكل مهمة: CPU/RAM/disk/I/O/model/media.
- [ ] bin-packing ديناميكي.
- [ ] دمج القياس الحالي للضغط.
- [ ] منع OOM/disk exhaustion.
- [ ] اختبارات workloads مختلطة.

# T23 — Priority & Fairness

- [ ] low/normal/high/urgent.
- [ ] aging.
- [ ] fairness per owner.
- [ ] resource weighting.
- [ ] منع starvation.
- [ ] interactive tasks لا تُحجب طويلًا خلف schedule ثقيلة.

# T24 — Cancellation حقيقية

CancellationToken من Telegram حتى TaskService/OpenCode/subprocess/media/upload.

- [ ] cooperative cancellation.
- [ ] cleanup لكل الموارد.
- [ ] kill escalation مضبوط للعمليات الفرعية.
- [ ] no zombie processes.
- [ ] اختبارات cancel بكل مرحلة.

# T25 — Progress Protocol موحد

Events: queued/downloading/analyzing/planning/executing/waiting_model/processing_media/uploading/completed.

- [ ] structured events.
- [ ] renderer منفصل.
- [ ] debounce/rate-limit لتحديث Telegram.
- [ ] الرسالة الواحدة تبقى المبدأ.
- [ ] persisted progress بعد restart.

# T26 — نتائج طويلة بلا قص

- [ ] لا فقدان لأي محتوى بسبب حد رسالة Telegram.
- [ ] summary في الرسالة.
- [ ] full result كـ .md/.txt أو artifact.
- [ ] paginated view اختياري.
- [ ] output manifest.
- [ ] اختبارات نتائج ضخمة.

# T27 — Telegram UX v2

- [ ] pagination.
- [ ] inline keyboard.
- [ ] breadcrumbs.
- [ ] confirmations.
- [ ] task history.
- [ ] output browser.
- [ ] schedule browser.
- [ ] retry/cancel/duplicate/rerun/download buttons.
- [ ] الأوامر النصية تبقى مدعومة بالكامل.

# T28 — User Preferences

جدول `user_settings`:

- [ ] timezone.
- [ ] language.
- [ ] notification level.
- [ ] default workspace.
- [ ] default execution profile.
- [ ] model preference.
- [ ] output style.
- [ ] retention.
- [ ] schedule defaults.

# T29 — Observability كاملة

Metrics:

- [ ] queue depth.
- [ ] task latency/duration.
- [ ] retry count.
- [ ] model/Telegram failures.
- [ ] DB latency/locks.
- [ ] memory/CPU/disk.
- [ ] attachment throughput.
- [ ] active workers.
- [ ] schedule lag.
- [ ] Prometheus endpoint اختياري.

# T30 — Distributed Tracing

- [ ] OpenTelemetry.
- [ ] trace_id من Telegram إلى DB/OpenCode/model/media/GitHub/result.
- [ ] context propagation.
- [ ] sampling policy.
- [ ] privacy redaction.

# T31 — Logging احترافي

- [ ] structured JSON logs.
- [ ] event/task/schedule/owner_hash/trace/attempt/component/duration/status.
- [ ] عدم تسجيل prompt كامل افتراضيًا.
- [ ] secret/file-content redaction.
- [ ] rotation/retention.

# T32 — Health / Readiness / Liveness

- [ ] liveness مستقل.
- [ ] readiness مستقل.
- [ ] health شامل Telegram/DB/OpenCode/disk/workers/scheduler/model catalog.
- [ ] systemd readiness integration حيث يمكن.
- [ ] degraded-mode semantics.

# T33 — Backup & Disaster Recovery

- [ ] SQLite online backup API.
- [ ] schedules/user settings/metadata/config غير السري.
- [ ] backup verify.
- [ ] automated restore test.
- [ ] أهداف RPO/RTO موثقة.
- [ ] تشفير backups عند الحاجة.

# T34 — Atomic Deployment

releases/version + current symlink.

- [ ] download.
- [ ] verify.
- [ ] test.
- [ ] migrate DB.
- [ ] health check.
- [ ] atomic switch.
- [ ] restart.
- [ ] smoke test.
- [ ] automatic rollback.

# T35 — Database Migration Rollback

- [ ] version/checksum.
- [ ] backup requirement.
- [ ] compatibility window.
- [ ] forward migration.
- [ ] rollback/restore strategy.
- [ ] self-update لا ينفذ migration خطرة دون snapshot.

# T36 — Security Hardening

- [ ] localhost OpenCode.
- [ ] systemd hardening: NoNewPrivileges/PrivateTmp/filesystem protections/capability minimization/resource accounting حسب التوافق.
- [ ] file magic inspection.
- [ ] zip bomb limits.
- [ ] symlink/path traversal protection.
- [ ] output jail.
- [ ] secret redaction.
- [ ] max subprocess runtime.

# T37 — Secret Management

- [ ] دعم systemd credentials اختياريًا.
- [ ] encrypted local credential store أو secrets manager عند الحاجة.
- [ ] أقل مدة exposure ممكنة.
- [ ] migration من .env بدون كسر deployment الحالي.

# T38 — Supply Chain Security

- [ ] dependency vulnerability scan.
- [ ] secret scanning.
- [ ] static analysis.
- [ ] SBOM.
- [ ] artifact attestations.
- [ ] release checksums.
- [ ] Sigstore عند الملاءمة.

# T39 — Code Quality Gates

- [ ] Ruff.
- [ ] mypy أو pyright.
- [ ] formatting.
- [ ] import checks.
- [ ] dead-code detection.
- [ ] complexity limits.
- [ ] CI enforcement تدريجي لا يكسر main بلا migration واضحة.

# T40 — Test Architecture

تقسيم: unit/integration/contract/e2e/load/fault/security/migration.

- [ ] contract tests بين bridge/OpenCode.
- [ ] Telegram abstraction tests.
- [ ] E2E Telegram mock → queue → OpenCode mock → output.
- [ ] fixtures مستقرة.

# T41 — Fault Injection

اختبارات: bot kill/OpenCode kill/Telegram outage/SQLite busy/disk full/corrupt attachment/429/500/timeout/duplicate update/server restart أثناء schedule/worker crash/output upload failure.

- [ ] إثبات recovery لكل حالة.
- [ ] عدم فقد committed task.

# T42 — Load & Soak Testing

- [ ] آلاف المهام.
- [ ] مئات schedules.
- [ ] ملفات كبيرة.
- [ ] أوامر ضخمة.
- [ ] 24–72h soak.
- [ ] parallel owners.
- [ ] slow OpenCode/disk.
- [ ] memory leak checks.

# T43 — Performance Targets

- [ ] لا فقدان committed task.
- [ ] لا duplicate schedule occurrence.
- [ ] ACK نصي <1s عادةً ضمن البيئة المرجعية.
- [ ] DB lock failures = صفر في الحمل المستهدف.
- [ ] worker crash recovery تلقائي.
- [ ] restart لا يفقد schedules/drafts.
- [ ] RAM للملف الكبير شبه ثابتة.
- [ ] عشرات آلاف queue records دون تدهور كبير.
- [ ] critical paths لها metrics.
- [ ] هدف uptime موثق وقابل للقياس.

# T44 — إزالة الحدود الداخلية القديمة

- [ ] تحويل attachment count/total bytes/pending seconds/workers وغيرها إلى policies.
- [ ] دعم 0=unlimited by application policy وauto=resource-calculated حيث مناسب.
- [ ] Resource Guard يبقى مانعًا لـ OOM/disk exhaustion.
- [ ] migration compatibility للمتغيرات القديمة.

# T45 — Local Telegram API كخيار Production

- [ ] deployment profile مستقل.
- [ ] cloud يبقى default.
- [ ] capability detection في /health.
- [ ] service hardening.
- [ ] file size/load tests.

# T46 — إزالة ازدواجية Legacy/V3

- [ ] production path واحد.
- [ ] config واحد.
- [ ] task engine واحد.
- [ ] client واحد.
- [ ] migration path.
- [ ] حذف legacy فقط بعد إثبات عدم استخدامه.

# T47 — Documentation as Code

- [ ] architecture.
- [ ] DB schema.
- [ ] state machine.
- [ ] scheduler semantics.
- [ ] failure semantics.
- [ ] attachment pipeline.
- [ ] security profiles.
- [ ] deployment.
- [ ] disaster recovery.
- [ ] troubleshooting.
- [ ] كل feature كبيرة تحدث الوثائق في نفس التغيير.

# T48 — Migration من v1.8 إلى 2.0

- [ ] DB backup أولًا.
- [ ] sessions.
- [ ] schedules.
- [ ] pending attachments.
- [ ] user IDs.
- [ ] منع تشغيل schedule قديمة مرتين.
- [ ] dry-run.
- [ ] migration report.
- [ ] rollback test.

# T49 — Canary

- [ ] shadow بدون تنفيذ.
- [ ] مقارنة scheduler.
- [ ] نسبة صغيرة من المهام.
- [ ] مراقبة metrics.
- [ ] توسعة تدريجية.
- [ ] rollback تلقائي عند thresholds.

# T50 — إصدار OpenCode Bridge 2.0

لا يغلق إلا بعد:

- [ ] كامل CI.
- [ ] Python compatibility matrix.
- [ ] migration test.
- [ ] backup/restore test.
- [ ] crash recovery.
- [ ] schedule recovery.
- [ ] duplicate delivery.
- [ ] file streaming.
- [ ] ملف كبير عبر Local Bot API.
- [ ] long-prompt.
- [ ] multi-workspace.
- [ ] 24–72h soak.
- [ ] لا high/critical dependency vulnerabilities غير مقبولة.
- [ ] release rollback مجرب.
- [ ] التوثيق مكتمل.
- [ ] tag/release 2.0.0 ناجح.

---

## ترتيب التنفيذ الإجباري

T00 → T01 → T02 → T03 → T04 → T05 → T06 → T07 → T08 → T09 → T10 → T11 → T12 → T13 → T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21 → T22 → T23 → T24 → T25 → T26 → T27 → T28 → T29 → T30 → T31 → T32 → T33 → T34 → T35 → T36 → T37 → T38 → T39 → T40 → T41 → T42 → T43 → T44 → T45 → T46 → T47 → T48 → T49 → T50.

**ممنوع العمل المتوازي بين المراحل.** يمكن تنفيذ بنود داخل المرحلة الحالية فقط، ولا تُعلّم المرحلة مكتملة إلا بعد تحقق جميع بنودها ومعايير قبولها ونجاح CI المتعلق بها.
