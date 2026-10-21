# OpenCode Bridge v1.8.x — Architecture Baseline قبل 2.0

هذه الوثيقة هي لقطة مرجعية للحالة الإنتاجية قبل بدء refactor الخاص بخطة OpenCode Bridge 2.0. الغرض منها تثبيت الواقع الحالي، وليس وصف الحالة المستهدفة.

## 1. مسار التشغيل الإنتاجي

خدمة Telegram الإنتاجية هي:

- الوحدة: `deploy/opencode-bridge-telegram.service`
- WorkingDirectory: `/home/ubuntu/opencode-bridge`
- EnvironmentFile: `/home/ubuntu/opencode-bridge/.env`
- ExecStart: `/home/ubuntu/opencode-bridge/venv/bin/python -m run_v3`
- Restart: `on-failure`

المسار الفعلي:

```text
systemd
  -> python -m run_v3
      -> import bot as core
      -> workspace_runtime.install(...)
      -> bot.post_init(...)
          -> SessionStore / TaskQueueStore
          -> TaskService
          -> ModelManager
          -> attachment cleanup
      -> v3_plugin.install(...)
      -> ci_plugin.install(...)
      -> resource_commands.install(...)
      -> watchdog_plugin.install(...)
      -> Telegram polling
```

`bot.py` هو قلب التطبيق الحالي، لكنه ليس entrypoint الإنتاج المباشر. `run_v3.py` يضيف workspace/CI/resource/watchdog integrations فوقه.

## 2. المكونات الحالية

| الملف | المسؤولية الحالية |
| --- | --- |
| `bot.py` | Telegram commands، orchestration، task execution، attachments، scheduling، model/session integration |
| `run_v3.py` | production bootstrap وV3 plugin installation |
| `v3_plugin.py` | اختيار مستودع GitHub وربط workspace بالطلبات |
| `task_queue.py` | SQLite queue + persistent schedules + pending attachments |
| `task_service.py` | production adaptive task service |
| `task_service_v3.py` | bounded worker pool |
| `session_store.py` | OpenCode session persistence |
| `workspace_store.py` | active workspace persistence |
| `workspace_manager.py` | repository/workspace management |
| `workspace_runtime.py` | workspace scoping around OpenCode |
| `opencode_client.py` | HTTP client إلى OpenCode |
| `attachments.py` | managed attachment intake/integrity/work/output |
| `progress.py`, `progress_reporter.py` | live/persisted task progress |
| `model_manager.py`, `agent_scout.py`, `model_catalog.py` | model discovery/routing/scouting |
| `resource_monitor.py`, `adaptive_workers.py` | host pressure and worker admission |
| `watchdog_runner.py`, `watchdog_plugin.py`, `watchdog_policy.py` | watchdog |
| `github_ci.py`, `ci_plugin.py` | GitHub Actions status integration |
| `free_points.py` | local free-tier usage estimate |
| `audit_log.py` | structured audit JSONL |

## 3. SQLite databases والجداول

### `sessions.db`

يستخدمه حاليًا أكثر من Store عبر اتصالات مستقلة.

#### `sessions`

```sql
telegram_user_id TEXT PRIMARY KEY
opencode_session_id TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
model TEXT
title TEXT
```

المصدر: `session_store.py`.

#### `agent_tasks`

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
owner_id TEXT NOT NULL
chat_id INTEGER NOT NULL
prompt TEXT NOT NULL
status TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
due_at TEXT
repeat_seconds INTEGER
sequence INTEGER NOT NULL DEFAULT 0
started_at TEXT
completed_at TEXT
last_error TEXT
attachments_json TEXT NOT NULL DEFAULT '[]'
activity_json TEXT NOT NULL DEFAULT '[]'
execution_mode TEXT
status_message_id INTEGER
schedule_job_id INTEGER
```

Indexes:

- `idx_agent_tasks_owner_status_sequence(owner_id, status, sequence, id)`
- `idx_agent_tasks_status_due(status, due_at)`
- `idx_agent_tasks_owner_created(owner_id, created_at)`

#### `scheduled_jobs`

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
owner_id TEXT NOT NULL
chat_id INTEGER NOT NULL
name TEXT NOT NULL COLLATE NOCASE
prompt TEXT NOT NULL
enabled INTEGER NOT NULL DEFAULT 1
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
next_run_at TEXT
repeat_seconds INTEGER
timezone_name TEXT NOT NULL DEFAULT 'UTC'
last_run_at TEXT
last_error TEXT
UNIQUE(owner_id, name)
```

Indexes:

- `idx_scheduled_jobs_owner_name(owner_id, name COLLATE NOCASE)`
- `idx_scheduled_jobs_due(enabled, next_run_at)`

#### `pending_attachment_batches`

```sql
owner_id TEXT NOT NULL
chat_id INTEGER NOT NULL
attachments_json TEXT NOT NULL DEFAULT '[]'
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
expires_at TEXT NOT NULL
PRIMARY KEY(owner_id, chat_id)
```

Index: `idx_pending_attachment_expiry(expires_at)`.

المصدر للجداول الثلاثة: `task_queue.py`.

#### `active_workspaces`

```sql
owner_id TEXT PRIMARY KEY
repo_slug TEXT NOT NULL
directory TEXT NOT NULL
updated_at TEXT NOT NULL
```

المصدر: `workspace_store.py`.

### `runtime/free-points.db`

#### `free_points_usage`

```sql
day TEXT PRIMARY KEY
used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0)
exhausted INTEGER NOT NULL DEFAULT 0 CHECK (exhausted IN (0, 1))
updated_at TEXT NOT NULL
```

المصدر: `free_points.py`.

### ملاحظة SQLite مهمة

- `WorkspaceStore` يضبط `journal_mode=WAL` و`synchronous=NORMAL` و`busy_timeout=5000` على اتصاله.
- `FreePointsTracker` يضبط `journal_mode=WAL` و`busy_timeout=5000`.
- `SessionStore` و`TaskQueueStore` لا يطبقان حاليًا نفس PRAGMA على اتصالاتهما.
- migration الحالي في `TaskQueueStore.init()` يعتمد على `PRAGMA table_info` و`ALTER TABLE` الشرطي، وليس نظام schema migrations مستقل.

هذه النقطة baseline وليست إصلاح T04 بعد.

## 4. أوامر Telegram في مسار الإنتاج

### Core

`start, new, reset, abort, stop, tasks, progress, trace, cancel, discard, schedule, repeat, schedules, schedshow, schedrename, schededit, schedappend, schedtime, schedinterval, schedrun, schedpause, schedresume, scheddelete, model, status, health, agents, maintenance, search, deepresearch, extreme, news, compare, factcheck, verify, open, extract, share, unshare, help`

### V3 / plugins

`use, repo, repos, sync, dev, ci, resources`

إجمالي الواجهة المسجلة في baseline: **47 أمرًا معروفًا**.

تم توحيد `dev` وworkspace text أثناء T00 مع مسار الرسالة الواحدة؛ لا تعرض هذه المسارات task ID أو queue position للمستخدم.

## 5. متغيرات البيئة الموثقة

الاتحاد الحالي بين `.env.example` و`.env.v3.example`:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_USERS
TELEGRAM_ALLOWED_CHAT_IDS
TELEGRAM_PROXY_URL
TELEGRAM_ATTACHMENT_MAX_BYTES
TELEGRAM_ATTACHMENT_MAX_COUNT
TELEGRAM_ATTACHMENT_MAX_TOTAL_BYTES
TELEGRAM_ATTACHMENT_PENDING_SECONDS
TELEGRAM_MEDIA_GROUP_DEBOUNCE_SECONDS
TELEGRAM_DAILY_TASK_COUNTER_TIMEZONE
OPENCODE_HOST
OPENCODE_PORT
OPENCODE_PASSWORD
OPENCODE_SERVER_USERNAME
OPENCODE_SERVER_PASSWORD
OPENCODE_DEFAULT_MODEL
OPENCODE_MODEL_VARIANT
OPENCODE_VARIANT_MODEL
OPENCODE_PIN_DEFAULT_MODEL
OPENCODE_AGENT
OPENCODE_MODEL_SYNC_SECONDS
OPENCODE_FREE_DAILY_POINTS
AGENT_SCOUT_INTERVAL_SECONDS
AGENT_SCOUT_AUTO_STRONGEST
AGENT_SCOUT_PREFERRED_AGENT
AGENT_SCOUT_WEB_RESEARCH
GITHUB_WORKSPACE_ROOT
GITHUB_ALLOWED_REPOS
GITHUB_TOKEN
GITHUB_CI_WORKFLOW
AGENT_TASK_WORKERS
AGENT_TASK_POLL_SECONDS
AGENT_ADAPTIVE_WORKERS
WATCHDOG_INTERVAL_SECONDS
WATCHDOG_STALE_TASK_SECONDS
WATCHDOG_RESTART_COOLDOWN_SECONDS
WATCHDOG_MAX_RESTARTS_PER_HOUR
LOG_LEVEL
```

مرجع تنفيذي موجود في الكود وغير موجود حاليًا في ملفات المثال:

- `AGENT_WORKER_RECOVERY_SECONDS`.

إعدادات صيانة/host إضافية مستخدمة في scripts:

```text
OPENCODE_SWAPFILE_GIB
OPENCODE_SWAPFILE_PATH
OPENCODE_SWAP_MIN_FREE_GIB
OPENCODE_ZRAM_MAX_MIB
OPENCODE_ZRAM_PERCENT
REBOOT_COMMAND
REBOOT_WAIT_SECONDS
TELEGRAM_CHAT_ID
TELEGRAM_MAINTENANCE_CHAT_ID
```

توجد أيضًا متغيرات shell داخلية محسوبة مثل `BRIDGE_DIR`, `RUNTIME_DIR`, `RUN_ID`, `EUID` وليست إعدادات مستخدم عامة.

الأداة `scripts/v2_baseline_probe.py` تعيد جرد أسماء environment references دون قراءة قيم الأسرار.

## 6. حدود المرفقات الحالية

من `attachments.py` و`.env.example`:

| الحد | baseline |
| --- | ---: |
| ملف وارد واحد | 20 MiB |
| عدد المرفقات للمهمة | 10 |
| الحجم الإجمالي للمهمة | 50 MiB |
| الملفات الناتجة المرسلة تلقائيًا | 10 |
| انتظار الأمر بعد ملف بلا Caption | 600 ثانية |
| media-group debounce | 1.25 ثانية |

المسارات المُدارة:

```text
runtime/attachments/incoming/
runtime/attachments/work/
runtime/attachments/outgoing/
```

سلامة الملف الحالية:

- اسم ملف sanitized.
- SHA-256 للمرفق.
- إعادة التحقق من المسار والحجم والبصمة قبل التنفيذ.
- منع symlink/path escape في managed paths.
- محتوى المرفق يعامل كبيانات غير موثوقة.
- original attachment لا يُنفذ.
- scratch per task.
- final output يجب أن يكون داخل outgoing الخاص بالمهمة.

الملفات المرئية مباشرة للنموذج حسب المنطق الحالي:

- `text/*`
- JSON/XML/JavaScript/YAML
- SVG
- PNG/JPEG/GIF/WebP

أما PDF/audio/video/binaries فتعالج عبر المسار المحلي وأدوات موجودة مسبقًا.

## 7. OpenCode policy baseline

كل من `opencode.json` و`opencode-v3.json` متطابقان في baseline.

الخادم:

- hostname: `127.0.0.1`
- port: `4096`
- mDNS: disabled
- share: manual
- snapshot: enabled
- default agent: `development-agent`

الصلاحيات العامة:

- read/edit عمومًا allow.
- deny لـ `.env`, `.env.*`, `*.pem`, `*.key`.
- webfetch/websearch/lsp/task/skill/external_directory مسموحة.
- doom loop = ask.

bash deny baseline يشمل:

- npm/pnpm/yarn installs/builds.
- webpack/rollup/vite/esbuild/tsc builds.
- pip installs.
- make/cmake/ninja.
- cargo/go/maven/gradle/javac.
- docker/flutter/dotnet builds.
- force push.
- hard reset/clean.
- remote URL/auth destructive operations.
- repository delete/PR merge.
- shutdown/reboot/halt/poweroff.

هذه القيود ستتحول لاحقًا وفق T19/T20 إلى execution profiles وsandbox، ولا تُحذف مباشرة على host.

## 8. worker/resource baseline

- `AGENT_TASK_WORKERS` default = 2.
- hard clamp الحالي = 1..8.
- poll default = 5s، clamp = 0.5..60s.
- recovery default = 30s، clamp = 1..600s.
- production `TaskService` يستخدم `StabilizedWorkerLimit` + `HostResourcePolicy` + shadow audit.
- الضغط يخفض admission فورًا، والتعافي يزيد تدريجيًا.
- running tasks لا تلغى لمجرد خفض worker limit.

## 9. systemd units الحالية

### user stack

1. `opencode-serve.service`
   - OpenCode على `127.0.0.1:4096`
   - Restart=on-failure

2. `opencode-bridge-telegram.service`
   - `python -m run_v3`
   - Restart=on-failure

3. `opencode-bridge.target`
   - يجمع الخدمتين.

### root/maintenance stack

4. `opencode-bridge-maintenance.service`
5. `opencode-bridge-maintenance.timer`
   - `08:30 UTC`
   - random delay حتى 20m
6. `opencode-bridge-reboot-guard.service`
7. `opencode-bridge-resource-optimizer.service`

## 10. CI / Release baseline

CI الحالي:

- runner: `ubuntu-26.04`
- Python: 3.12
- install `requirements.txt`
- compileall.
- unittest discovery.
- shell syntax.
- `opencode.json` validation.
- `opencode-v3.json` validation.
- semantic version check.

Release:

- workflow_run بعد CI ناجح على main.
- يقرأ VERSION.
- release notes من `releases/vX.Y.Z.md` أو CHANGELOG.
- ينشئ tag/release فقط إذا غير موجود.

## 11. Baseline regression suite

قبل 2.0 يوجد أصلًا test suite واسع. أضيف T00:

- `tests/test_v2_baseline_contract.py`

ليثبت خصوصًا:

- production entrypoint.
- جداول SQLite الستة.
- حدود attachments الحالية.
- تطابق OpenCode configs.
- deny policy الأساسية.
- single-message lifecycle في V3.
- persistent named schedule API.
- systemd unit inventory.

## 12. Baseline probe / benchmark

`scripts/v2_baseline_probe.py` أداة standard-library-only.

### Inventory

تستخرج بدون قراءة أسرار:

- Python modules.
- tests.
- Telegram commands.
- أسماء env الموثقة والمستخدمة.
- SQLite table names.
- systemd unit metadata.
- attachment constants.
- OpenCode policy summary.

### Micro-benchmark

يقيس على البيئة التي يعمل فيها:

- Python/platform/CPU count.
- peak RSS قبل/بعد.
- SHA-256 throughput كـCPU micro-benchmark.
- SQLite WAL insert throughput.
- SQLite indexed point-select throughput.

الأرقام ليست performance SLA؛ هي مرجع مقارنة قبل وبعد refactor على نفس نوع runner/host.

## 13. Known architectural debt المثبت في baseline

هذه ليست مراحل منفذة بعد، بل نقاط ستعالجها الخطة اللاحقة:

1. `bot.py` كبير ويجمع مسؤوليات كثيرة.
2. أكثر من Store يفتح connections منفصلة لنفس `sessions.db`.
3. PRAGMA غير موحدة بين stores.
4. migration logic موزع داخل init.
5. JSON blobs تستخدم لبعض البيانات التي ستحتاج query أفضل في 2.0.
6. worker ownership ليس lease/heartbeat durable بعد.
7. scheduling ليس cron/DST/misfire engine كامل بعد.
8. limits كثيرة hard-coded/clamped.
9. production path يضيف V3 plugins فوق core بدل architecture موحدة.
10. `opencode.json` و`opencode-v3.json` ازدواجية متطابقة حاليًا.
11. long Telegram result لا يملك artifact fallback كاملًا بعد.
12. file transfer ليس streaming abstraction كاملة بعد.

لا تعالج هذه البنود خارج ترتيب الخطة؛ تسجل هنا لمنع ضياع سبب المراحل اللاحقة.

## 14. شرط إغلاق T00

T00 لا تغلق إلا عندما:

- هذه الوثيقة والجرد موجودان.
- regression baseline ناجح.
- baseline probe/benchmark يعمل في CI.
- CI على commit إغلاق T00 ناجح.
- ملف حالة التنفيذ يحمل commit وCI evidence.
