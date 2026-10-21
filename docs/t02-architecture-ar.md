# T02 — تفكيك `bot.py` وإعادة بناء Architecture

> المرجع التنفيذي: `docs/opencode-bridge-2.0-execution-plan-ar.md`.
> هذه الوثيقة توثق حدود T02 فقط، ولا تُقدّم أي تنفيذ من T03 أو المراحل اللاحقة.

## النتيجة المعمارية

أصبح مسار الإنتاج مقسومًا إلى طبقات واضحة بدل وضع منطق Telegram وOpenCode والجدولة والمرفقات في ملف واحد:

```text
bridge/
  telegram/
    app.py
    middleware.py
    execution.py
    attachments.py
    commands/
      agent.py
      tasks.py
      schedules.py
      workspaces.py
      ci.py
      resources.py
      system.py
    callbacks/
      reboot.py
    rendering/
  domain/
    tasks/
    schedules/
    attachments/
    sessions/
    policies/
  services/
    task_service.py
    task_execution_service.py
    schedule_service.py
    media_service.py
    agent_service.py
    workspace_service.py
    ci_service.py
    resource_service.py
    maintenance_service.py
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

## حدود الطبقات

### Telegram

طبقة `bridge/telegram` مسؤولة فقط عن:

- ترجمة `Update` و`ContextTypes` إلى استدعاءات services.
- تنسيق الرسائل والـrendering.
- إنشاء/تعديل رسالة الحالة الواحدة.
- إرسال الملفات للمستخدم.
- تسجيل handlers وBotCommands.
- access middleware الخاص بTelegram.
- compatibility callbacks.

لا يُسمح لها بامتلاك SQL أو قواعد persistence أو قواعد اختيار جلسة OpenCode.

### Domain

`bridge/domain` لا يستورد Telegram أو `bot.py`.

يوجد حاليًا:

- request policies.
- schedule parsing/formatting.
- task status concepts.
- boundaries للمرفقات والجلسات.

التوسع التفصيلي للـdomain models يتم فقط عندما تصل المراحل المختصة، وليس داخل T02.

### Services

طبقة `bridge/services` لا تستورد Telegram أو `bot.py`.

أهم الخدمات بعد T02:

- `AgentService`: session lifecycle، model overview، input-aware model selection، prompt fallback.
- `TaskApplicationService`: validation وenqueue وtask operations.
- `TaskExecutionService`: دورة تنفيذ مهمة queue كاملة خلف ports مستقلة عن Telegram.
- `ScheduleService`: قواعد الجدولة والتعديل والتشغيل الفوري.
- `MediaTaskService`: staging/merge/queue/expiry للمرفقات.
- `WorkspaceService`: اختيار workspace والتحقق منه والمزامنة وتجهيز task context.
- `CIStatusService`: جلب حالة CI للمشروع النشط.
- `ResourceStatusService`: snapshot لتشخيص الموارد.
- `MaintenanceReportService`: قراءة تقرير الصيانة.
- `NotificationService`: transport-independent notification boundary.

## مسار تنفيذ المهمة

المسار الإنتاجي أصبح:

```text
Telegram Update
  -> thin command adapter
  -> TaskApplicationService / WorkspaceService / MediaTaskService
  -> queue
  -> TaskService worker
  -> bot._execute_agent_task (compatibility wrapper فقط)
  -> TaskExecutionService
  -> AgentService
  -> OpenCode client
  -> TelegramExecutionDelivery
```

`TaskExecutionService` لا يعرف Telegram. الإرسال النهائي والتقدم وtyping indicator موجودة في `TelegramExecutionDelivery`.

## OpenCode

منطق variant fallback وattachment transport fallback وmodel fallback نُقل إلى `AgentService`.

`opencode_client.py` و`bridge/services/agent_service.py` و`bridge/services/task_execution_service.py` لا تعتمد على Telegram.

## Scheduler

قواعد إنشاء/تعديل/إيقاف/استئناف/تشغيل الجداول داخل `ScheduleService`.

Telegram schedule handlers هي adapters فقط.

الـscheduler v2 وcron/timezone/DST ليست جزءًا من T02 وتبقى لـT07.

## Attachment pipeline

`MediaTaskService` يملك:

- validation.
- pending staging.
- merge.
- expiry.
- restore بعد فشل enqueue.
- queueing.

`TelegramMediaAdapter` يملك فقط تنزيل Telegram وتجميع media groups وربط الرسالة بالservice.

StorageBackend v2 وstreaming الكبير لا يُنفذان هنا؛ موضعهما T10–T12.

## Workspace / V3 compatibility

`v3_plugin.py` بقي كواجهة تركيب production compatibility فقط.

قواعد workspace انتقلت إلى `WorkspaceService`، وhandlers إلى `WorkspaceCommands`.

هذا يحافظ على `run_v3` الحالي وعدم كسر systemd production entrypoint أثناء T02.

## Compatibility layer

لم تُحذف مسارات الإنتاج القديمة قبل تغطيتها:

- `bot.py` يحتفظ بأسماء handlers/functions التي تعتمد عليها `run_v3` والاختبارات القديمة.
- handlers القديمة أصبحت delegates رفيعة.
- `_execute_agent_task` بقي wrapper رفيعًا فوق `TaskExecutionService`.
- `v3_plugin.py`, `ci_plugin.py`, `resource_commands.py` بقيت install/compatibility surfaces.

إزالة ازدواجية Legacy/V3 بالكامل مؤجلة صراحة إلى T46.

## حدود لم تُنفذ عمدًا في T02

حتى لا يُكسر التسلسل الإلزامي:

- typed central settings: T03.
- database unification/migrations: T04.
- durable queue v2: T05.
- scheduler v2: T07.
- storage backends: T11.
- OpenCode client v2: T15.
- observability infrastructure: T29.
- legacy/V3 deletion: T46.

الحزم `config`, `infrastructure/database`, `storage`, `metrics` موجودة كحدود معمارية فقط؛ التنفيذ الوظيفي ينتظر مرحلته.

## اختبارات T02

الاختبارات التي تثبت الحدود والسلوك تشمل:

- `tests/test_v2_architecture_boundaries.py`
  - domain/services لا تستورد Telegram أو bot.
  - كل حدود الهيكل المستهدف موجودة.
  - command adapters لا تحتوي SQL.
  - OpenCode/task execution layers لا تستورد Telegram.
  - كل handlers في compatibility entrypoints رفيعة.
- `tests/test_v2_task_architecture.py`
  - task/agent handlers delegates.
  - factories المطلوبة موجودة.
- `tests/test_v2_media_architecture.py`
  - media service مستقل.
  - media state خرج من bot.
- `tests/test_v2_task_execution_service.py`
  - success.
  - empty-response retry/failure.
  - cancelled-before-execution.
  - cleanup.
- `tests/test_v2_workspace_service.py`
  - selection/persistence.
  - tampered path rejection.
  - trusted context preparation.
- `tests/test_v2_agent_service.py`
  - session lifecycle/status.

## شرط الإغلاق

لا تُعتبر T02 مغلقة حتى ينجح كامل CI على Python 3.12/3.13/3.14 بعد جميع تغييرات T02 وتُحدّث وثيقة الحالة بالـSHA ورابط CI النهائي.
