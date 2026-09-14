## [1.3.0] - 2026-09-14

### Highlights

- Upgraded adaptive worker management from one-time startup sizing to continuous live admission control in the V3 task worker pool.
- Workers now re-evaluate host pressure before claiming each new task, allowing concurrency to contract under memory, swap, CPU, PSI, or disk pressure and expand again after recovery without restarting the bridge.
- Preserved `AGENT_TASK_WORKERS` as the administrator-defined hard ceiling instead of permanently shrinking it during bootstrap.

### Reliability and Safety

- Running tasks are never cancelled merely because resource pressure increases; only new task claims are throttled.
- Adaptive sampling failures fail safely to one worker rather than allowing unbounded concurrency during uncertain host state.
- Disabled workers remain alive and periodically re-check the live policy, avoiding destructive process churn and enabling automatic recovery when pressure subsides.
- Existing per-owner serialization, workspace isolation, direct-to-`main` development policy, and no-local-build guardrails remain unchanged.

### Resource Policy

- The live controller continues to use the lightweight `HostResourcePolicy`, including available memory, Linux memory PSI, normalized CPU load, disk headroom, and swap occupancy.
- Sustained swap use now participates directly in worker throttling: high swap usage reduces concurrency and near-exhausted swap forces single-worker operation while ignoring very small swap devices.
- Resource reporting includes swap utilization so operators can correlate concurrency decisions with reclaim pressure.

### Verification

- Added deterministic tests proving that worker admission can change from three workers to one and later recover to four without recreating the task service.
- Added coverage proving the live limit cannot exceed the configured ceiling and provider failures fall back to one worker.
- GitHub Actions remains the source of truth for syntax, unit tests, OpenCode configuration, semantic-version validation, and release publication.

## [1.2.5] - 2026-09-14

### Highlights

- Added a permanent Daily OpenCode Agent Scout to the existing `ModelManager`; it runs automatically every 24 hours without adding another daemon or local build workload.
- The scout inspects the live OpenCode primary-agent list and the live OpenCode Zen catalog, then considers only models whose provider metadata explicitly reports zero cost.
- Added a fresh web-research pass that asks OpenCode to compare the current free allow-list using recent official OpenCode information and reputable coding-agent benchmarks.
- The research result is accepted only when it exactly matches a currently active zero-cost model ID returned by the live provider catalog; otherwise the bridge falls back to its deterministic capability ranking.

### Global Default Switching

- The repository-specific `development-agent` remains the preferred primary agent because it carries the bridge's direct-to-`main`, CI, workspace-isolation, and no-local-build operating policy.
- If that primary agent is unavailable, the scout falls back to OpenCode's primary `build` agent before considering other visible primary-capable agents; subagents and hidden agents are never promoted to the global default.
- A successful daily decision updates the live bridge `DEFAULT_AGENT` and `DEFAULT_MODEL`, so existing conversations use the new agent on their next turn and every queued or newly-created task uses it when execution begins.
- Every saved OpenCode conversation is immediately patched to the selected free model. An inference already generating at the exact moment of a switch is allowed to finish safely and uses the new default from its next turn rather than being interrupted mid-response.
- The selected free model is pinned inside `ModelManager`, preventing the faster catalog-reconciliation loop from immediately replacing the researched daily choice while it remains active and free.

### Persistence and Safety

- The latest validated decision is stored in `runtime/agent-scout.json` with restricted file permissions and is restored after a service restart only if both the saved primary agent and saved model still exist in the current live OpenCode catalogs.
- Paid or unpriced models cannot be selected by the scout, even if a research response recommends them.
- A stale, removed, or newly paid model is automatically rejected on the next live catalog check and normal free-model selection takes over.
- Research failures are non-disruptive: the bridge keeps operating and selects the strongest free candidate from the live deterministic ranking instead of leaving users without an agent.
- The scout creates only a temporary OpenCode research session and removes it after the decision; no user conversation is reused for autonomous research.

### Configuration

- `AGENT_SCOUT_INTERVAL_SECONDS` controls the persistent research cadence and defaults to `86400` seconds (24 hours).
- `AGENT_SCOUT_PREFERRED_AGENT` defaults to `development-agent`.
- `AGENT_SCOUT_WEB_RESEARCH=1` enables the daily web-research comparison; setting it to `0` keeps the same free-only guardrails while using deterministic live-catalog ranking only.

### Verification

- Added tests proving that subagents and hidden agents cannot become the default, the project development agent is preferred, research output cannot inject a paid model, and a daily scout decision is applied to every saved conversation.
- Added coverage proving that an explicitly configured paid preference can never override the live zero-cost OpenCode Zen catalog.
- Fixed a test-isolation issue discovered by CI where intentional live-default mutation from the scout leaked into a later import test; production switching behavior remains unchanged.
- GitHub Actions remains the source of truth for syntax, unit tests, OpenCode configuration, version validation, and release publication.

## [1.2.4] - 2026-09-14

### Highlights

- Added a continuous V3 watchdog loop that evaluates OpenCode, the Telegram bridge, queue health, host resources, and repository deployment state without running application builds.
- Added deterministic health scoring with `healthy`, `degraded`, and `critical` states plus explicit manual-review escalation for risky conditions.
- Added bounded self-healing for the OpenCode user service while delegating Telegram process recovery to its existing systemd `Restart=on-failure` policy.
- Added structured watchdog reports and a dedicated redacted JSONL audit trail under `runtime/` for post-incident analysis.

### Safe Recovery Guardrails

- Automatic recovery is restricted to a fixed service allow-list and never permits SSH, firewall, reboot, package-management, destructive filesystem, or force-push actions.
- OpenCode restarts are blocked when available memory or disk headroom is critically low.
- Restart cooldowns and an hourly restart ceiling prevent feedback loops when a service remains unhealthy.
- Long-running tasks are never killed automatically; tasks that exceed the stale threshold are escalated for manual review instead.
- The watchdog never attempts to restart the Telegram bridge from inside the bridge process; systemd remains the single owner of bridge crash recovery.

### Health Signals and Observability

- The watchdog samples memory availability, swap, CPU load, disk headroom, and Linux memory PSI through the existing lightweight resource monitor.
- Queue health includes queued/running counts and stale-running detection from the SQLite task store using read-only access.
- OpenCode health combines systemd service state with the local HTTP health endpoint.
- Deployment health records whether the deployed revision matches the working revision and whether the local checkout trails the known `origin/main` reference.
- Runtime tuning is configurable with `WATCHDOG_INTERVAL_SECONDS`, `WATCHDOG_STALE_TASK_SECONDS`, `WATCHDOG_RESTART_COOLDOWN_SECONDS`, and `WATCHDOG_MAX_RESTARTS_PER_HOUR`.

### Verification

- Added unit coverage for healthy scoring, dual-service critical outages, stale-task escalation, critical-resource restart blocking, unknown-service rejection, cooldown behavior, and restart-loop limits.
- Fixed a CI assertion discovered by the new policy tests by preserving the stricter classification: simultaneous OpenCode and Telegram outages remain `critical` rather than being downgraded.
- GitHub Actions remains the source of truth for complete verification and release publication.

## [1.2.3] - 2026-09-14

### Highlights

- Bound V3 OpenCode sessions and requests to the exact repository workspace selected by the Telegram bridge instead of relying on prompt text alone.
- Added per-async-task workspace scoping with `ContextVar`, preventing directory context from leaking between parallel workers.
- Added automatic session rebinding when the active repository changes, after a service restart, or when a stored session no longer matches the task workspace.
- Added strict validation of the bridge-generated `ACTIVE_WORKSPACE` block before any scoped OpenCode request is executed.

### Multi-Repository Isolation

- Every scoped OpenCode HTTP request now carries the workspace directory through the supported `x-opencode-directory` request context.
- Session-specific requests retain their workspace binding through a lightweight in-memory session-to-directory registry.
- Queued tasks keep the repository directory captured when they were created, so switching the active Telegram repository cannot silently redirect an already queued development task.
- Spoofed or mismatched workspace paths are rejected before execution when they do not resolve to the allow-listed repository location.

### Concurrency and Reliability

- Workspace context uses Python `ContextVar` isolation, so independent worker tasks can safely target different repositories at the same time.
- Workspace scope is always reset after the request context exits, preventing accidental cross-project contamination.
- The V3 bootstrap replaces the generic OpenCode client before the task service and model manager start, ensuring workspace-aware behavior is used throughout the production runtime.
- Existing direct-to-`main`, CI feedback, no-local-build, and repository allow-list policies remain unchanged.

### Verification

- Added deterministic tests for workspace header injection, context cleanup, session-directory restoration, valid trusted workspace parsing, and rejection of forged workspace paths.
- GitHub Actions remains the source of truth for full verification; no application build or dependency installation is performed on the control server.

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