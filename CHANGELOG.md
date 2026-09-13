## [1.2.2] - 2026-09-14

### Highlights

- Added a lightweight Linux host resource policy that samples available memory, swap, CPU load, disk headroom, and memory pressure-stall information without requiring privileged access.
- Added adaptive worker sizing during V3 startup so the agent no longer trusts a fixed concurrency value when the host is under pressure.
- Promoted direct-to-default-branch autonomous development as the standard publication workflow for the development agent.
- Added a lightweight GitHub Actions monitor and Telegram `/ci` command for the active workspace.
- Added a mandatory CI feedback loop so the agent waits for remote checks, inspects failures, applies the smallest safe repair, pushes the fix, and verifies CI again.
- Added the first optional ZRAM/swap resource-optimizer asset and a systemd unit as groundwork for autonomous host tuning.

### Development Automation

- The development agent now fetches remote metadata, identifies the repository default branch, fast-forwards clean workspaces, reviews diffs, writes professional English Conventional Commit messages, pushes without force, and verifies publication.
- Feature branches and pull requests are now opt-in instead of the default, matching the repository owner's direct-to-`main` workflow.
- `github_ci.py` can inspect or wait for GitHub Actions by repository, branch, commit SHA, and workflow name without running builds on the control server.
- CI failure summaries include failed jobs and steps so repair prompts can focus on the actual remote failure instead of guessing.
- `GITHUB_CI_WORKFLOW` can target a specific workflow, while an optional `GITHUB_TOKEN` raises REST API rate limits without being inserted into agent prompts.

### Performance

- Healthy hosts can retain the configured worker capacity while small-memory hosts automatically reduce concurrency to avoid OpenCode/Telegram memory contention.
- Hosts below 1.5 GiB RAM are capped to one worker; hosts below 3 GiB are capped to two workers.
- CPU saturation, low disk headroom, and Linux memory PSI signals further reduce the recommended concurrency when necessary.
- Resource sampling is cached to keep monitoring overhead negligible on small VPS deployments.
- GitHub CI monitoring is read-only and network-bound, adding no local compilation or dependency-heavy workload.

### Reliability and Safety

- Adaptive sizing is enabled by default and can be disabled with `AGENT_ADAPTIVE_WORKERS=0` when fixed concurrency is explicitly required.
- The configured `AGENT_TASK_WORKERS` remains the upper bound; automatic sizing only reduces unsafe concurrency and never exceeds the administrator-defined limit.
- Added deterministic unit coverage for CI success/pending states, failed-step extraction, repository validation, and adaptive resource policy behavior.
- Direct publication never permits force push, destructive Git cleanup, credential changes, or overwriting unrelated local modifications.
- The development cycle treats GitHub Actions as the source of truth whenever verification requires builds, dependency installation, packaging, or platform-specific compilation.

### Autonomous Host Roadmap

- Added a guarded ZRAM-first memory optimizer that can provision bounded compressed swap and a low-priority disk-swap fallback without overwriting an existing unknown swap file.
- Root-level activation remains intentionally separated from the unprivileged agent runtime while the installation path is hardened further.
- Follow-up work is tracked for live concurrency resizing, service health scoring, reversible self-healing, disk-pressure controls, and automated security/update posture.

## [1.2.1] - 2026-09-14

### Highlights

- Promoted the V3 multi-repository development agent from an optional architecture to the production Telegram service path.
- Promoted the hardened development-agent policy to the primary `opencode.json` configuration used by the existing OpenCode systemd service.
- Added safe repository selection, persistent active workspaces, automatic workspace context injection, and configurable bounded parallel task execution.
- Added a dedicated V3 runtime wrapper that reuses the mature bridge instead of duplicating Telegram, scheduling, model-selection, progress, and audit logic.
- Added a production-oriented environment template for repository allowlists, workspace location, and worker concurrency.

### Performance

- Independent owners can execute tasks concurrently through a bounded worker pool while the queue continues to serialize work for the same owner.
- Git workspace operations use per-repository locks to avoid synchronization races.
- SQLite workspace state uses WAL mode, normal synchronization, and a busy timeout to reduce contention on long-running VPS deployments.

### Reliability

- Added CI validation for Python syntax, the complete unit-test suite, both OpenCode configuration files, and semantic versioning.
- Repaired the CI workflow so jobs are created and executed reliably on `main` pushes.
- Reworked automated release publication so successful CI runs can publish GitHub Releases through the GitHub API-backed CLI flow.
- Added unit coverage for GitHub URL normalization, repository allowlisting, and workspace-root isolation.

### Security and Operational Controls

- Repository access is limited by `GITHUB_ALLOWED_REPOS` and all managed working copies stay under `GITHUB_WORKSPACE_ROOT`.
- The development agent denies secret/key files and blocks local builds, dependency installation, force pushes, destructive Git operations, credential changes, release merges, and system power operations.
- The control server remains an AI development/orchestration host; builds and dependency-heavy verification stay in CI rather than on the VPS.

### Deployment Notes

- The Telegram systemd unit now starts `run_v3`, which layers V3 capabilities over the existing bridge runtime.
- Existing OpenCode service wiring remains compatible because the production `opencode.json` now contains the V3 development-agent configuration.
- Configure `GITHUB_ALLOWED_REPOS`, `GITHUB_WORKSPACE_ROOT`, and optionally `AGENT_TASK_WORKERS` before enabling repository development workflows.

## [1.2.0] - 2026-09-14

### Highlights

- Activated the bounded parallel task worker pool in the production task service while preserving strict per-owner task ordering.
- Added environment-based worker and polling controls so throughput can be tuned without code changes.
- Added a GitHub Actions CI pipeline covering Python 3.11, 3.12, and 3.13 with syntax, unit-test, configuration, semantic-version, and shell-script validation.
- Prepared the repository for CI-gated automated tagging and GitHub Releases.
- Continued the V3 development-agent architecture with repository-focused prompts and persistent multi-repository workspace management.

### Performance

- Independent users can now execute agent tasks concurrently instead of sharing a single global serial worker.
- Worker concurrency is bounded to protect small VPS hosts and can be configured with `AGENT_TASK_WORKERS`.
- Idle queue polling is configurable with `AGENT_TASK_POLL_SECONDS` to balance responsiveness and resource usage.

### Reliability and Safety

- The queue continues to prevent concurrent execution for the same owner, keeping task order deterministic.
- CI now validates every push to `main` before an automated release can be published.
- Server-side build restrictions remain unchanged: development orchestration may edit, inspect, commit, and manage repositories without performing application builds on the control server.

### Upgrade Notes

The default configuration uses two task workers and a five-second idle poll interval. Existing deployments remain compatible; no database migration is required.

## [1.1.13] - 2026-08-22

### ملخص

إصلاح توافق حقل النموذج مع رسائل OpenCode ومنع خطأ 400

### التغييرات منذ v1.1.12

- fix: send OpenCode model reference as object (c9c780d)

## [1.1.12] - 2026-08-22

### ملخص

اختيار تلقائي للنموذج العام الأفضل ومزامنة توفر نماذج Zen

### التغييرات منذ v1.1.11

- feat: automatically reconcile best Zen model (4a66e02)

## [1.1.11] - 2026-08-22

### ملخص

إضافة أوامر بحث صريحة وتوجيه نية متدرج مع تحقق من المصادر

### التغييرات منذ v1.1.10

- feat: add intent-aware research commands (60713c4)

## [1.1.10] - 2026-08-22

### ملخص

تصفير عدّاد مهام اليوم تلقائيًا عند بداية اليوم مع حفظ السجل الكامل

### التغييرات منذ v1.1.9

- feat: reset displayed task count daily (45f04ea)

## [1.1.9] - 2026-08-22

### ملخص

تطوير البحث إلى منهج متدرج وموثّق مع تحقق متقاطع ومصادر ومستويات عمق تلقائية

### التغييرات منذ v1.1.8

- feat: add evidence-first deep research workflow (317952f)

## [1.1.8] - 2026-08-22

### ملخص

تحويل نافذة تقدم المهمة إلى عرض معلومات فقط دون أزرار أو إرشادات تحكم

### التغييرات منذ v1.1.7

- ui: make task progress informational only (08d3804)

## [1.1.7] - 2026-08-22

### ملخص

تشغيل الصيانة بصمت وإضافة حارس إعادة تشغيل مؤجل

### التغييرات منذ v1.1.4

- fix: mark root maintenance scripts executable (b33ca6c)
- release: v1.1.6 (97bd38c)
- fix: invoke root maintenance installer by absolute path (237b7ee)
- release: v1.1.5 (2040769)
- feat: run maintenance silently with guarded reboot flow (29748dd)

## [1.1.6] - 2026-08-22

### ملخص

تشغيل الصيانة بصمت وإضافة حارس إعادة تشغيل مؤجل

### التغييرات منذ v1.1.4

- fix: invoke root maintenance installer by absolute path (237b7ee)
- release: v1.1.5 (2040769)
- feat: run maintenance silently with guarded reboot flow (29748dd)

## [1.1.5] - 2026-08-22

### ملخص

تشغيل الصيانة بصمت وإضافة حارس إعادة تشغيل مؤجل

### التغييرات منذ v1.1.4

- feat: run maintenance silently with guarded reboot flow (29748dd)

## [1.1.4] - 2026-08-22

### ملخص

قصر قائمة النماذج المجانية على OpenCode Zen

### التغييرات منذ v1.1.3

- feat: limit model catalog to free OpenCode Zen models (7abf7c2)

## [1.1.3] - 2026-08-22

### ملخص

تبسيط رسالة البداية وإزالة النصوص التشغيلية من المساعدة

### التغييرات منذ v1.1.2

- fix: keep start message minimal and remove operational help text (9dec6f2)

## [1.1.2] - 2026-08-22

### ملخص

عرض النماذج المجانية ذات التكلفة الصفرية فقط

### التغييرات منذ v1.1.1

- feat: list only zero-cost models in bot catalog (16b8433)

## [1.1.1] - 2026-08-22

### ملخص

تعيين Muse Spark 1.2 كنموذج افتراضي للبوت

### التغييرات منذ v1.1.0

- config: default bot sessions to Muse Spark 1.2 (c03b09b)

## [1.1.0] - 2026-08-22

### ملخص

إضافة تقدم حي وسجل نشاط آمن وتحكم تفاعلي بالمهام

### التغييرات منذ v1.0.2

- feat: add safe live task progress to telegram (c0f0228)

## [1.0.2] - 2026-08-22

### ملخص

إصلاح استئناف النشر وتفعيل مسار الإصدار المتحقق

### التغييرات منذ v1.0.1

- fix: resume pending deployment from deployed revision (b020ab2)

# سجل التغييرات

## [1.0.1] - 2026-08-22

### ملخص

إضافة مستودع Git محلي ومسار إصدار ونشر وتراجع متحقق

### التغييرات منذ v1.0.0

- chore: add verified release workflow (fef393d)
