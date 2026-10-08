# Telegram Server Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a secure, resumable Telegram control plane for OpenCode Bridge, GitHub task execution, constrained server operations, and risk-based GitHub Actions deployment.
**Architecture:** Extend the existing Telegram panels, SQLite queue, services, GitHub client, and systemd deployment layout in small issue-scoped changes. GitHub Actions remains the only build/test environment; production receives a verified immutable artifact and supports backup, readiness checks, and rollback.
**Tech Stack:** Python 3.12–3.14, python-telegram-bot, SQLite, GitHub REST/Actions, GitHub-hosted Actions runners, systemd.
**Spec:** `docs/superpowers/specs/2026-10-08-telegram-control-plane-design.md`

## Global Constraints

- Never build, test, or install development dependencies on the production host. Use GitHub Actions for all validation and builds.
- Work one GitHub Issue at a time, in dependency order. Create a task branch from current `main`, implement, push, inspect checks for the exact PR SHA, merge only after required checks succeed, then delete that task branch. Close its Issue only after validating the merged SHA and acceptance criteria.
- Cap concurrency at three and apply current resource-pressure policy before starting more work. Persist checkpoints and pause on model quota exhaustion until the documented UTC reset; do not retry in a tight loop.
- Production deployment is automatic only after required CI/security gates and immutable artifact verification. Keep backups, readiness/smoke checks, automatic rollback, and a bounded retry policy. Never weaken main protection to enable merging.
- Require separate, scoped authorization for destructive repository actions, force pushes, privilege/secret changes, or irreversible production data operations. Do not expose a general shell through Telegram.
- Use signed HTTPS webhooks only when an authenticated ingress exists; otherwise use bounded polling with ETag/backoff.
- Report evidence by layer. Mark unavailable live Telegram, staging, restore, or production rollback evidence `NOT TESTED` rather than inferring success from CI.

## Review Focus

- RC versions never create stable releases; stable release evidence is evaluated on the exact target SHA.
- Missing credentials, scopes, private-chat identity, role, approval, webhook signature, or replay protection fail closed.
- Queue recovery and callbacks remain idempotent across retries and restarts; maximum concurrency is never exceeded.
- Deployment consumes the artifact for the merged SHA, creates a recoverable backup, verifies readiness, and restores the previous version when verification fails.
- UI status/progress and agent capabilities come from their source; unavailable data is explicit and no dangerous action is offered without its policy path.

---

## Ordered GitHub Issues

### Issue 1 — Correct stable/preview release selection

**Scope:** `.github/workflows/release.yml`, release workflow tests/validation, `VERSION`/release process documentation as needed.

- [ ] Add a regression check for a successful `main` CI run at `VERSION=2.0.0-rc.9`; assert it exits successfully as a deliberate preview skip and creates no stable tag/release.
- [ ] Change release selection so only a valid stable `x.y.z` version enters stable publication. Keep preview handling explicit and separate; do not silently treat RC as stable or fail ordinary CI because it is an RC.
- [ ] Preserve exact-SHA checkout, existing stable evidence gates, notes validation, and idempotency for an already published stable tag.
- [ ] Run workflow lint/contract tests in GitHub Actions and inspect the resulting `main` workflow run after merge.

**Acceptance:** An RC main commit produces no stable-release attempt; a stable commit retains evidence checks and publishes at most one matching tag/release; malformed versions fail clearly.

### Issue 2 — Durable deploy artifact and automatic rollback

**Scope:** `.github/workflows/deploy-production.yml`, `scripts/deploy.sh`, `scripts/rollback.sh`, `maintenance/self_update.py`, deployment tests/docs, GitHub environment configuration.

- [ ] Build and validate a versioned artifact in a GitHub-hosted runner; attach source SHA, version, and checksum. Fail deployment if the artifact identity differs from the merged `main` SHA.
- [ ] Replace production-side `compileall`, test execution, and dependency installation in the update path with artifact/checksum verification and a narrowly scoped activation command.
- [ ] Implement pre-deploy backup, atomic release-directory/symlink switch, bounded readiness/smoke checks, and automatic restoration of the previous release on failure. Keep logs and preserve the failed artifact for diagnosis.
- [ ] Ensure retry is bounded and only for transient transport/health failures; pause the deployment stream and notify on rollback or credential/configuration failures.
- [ ] Configure deployment secrets with least privilege and use GitHub-hosted runners; do not register a runner on the low-memory production host.
- [ ] Validate success, bad-checksum, failed-health, and rollback cases in isolated GitHub Actions fixtures before enabling production deployment.

**Acceptance:** A deployment can only activate the artifact for its exact merged SHA; production performs no build/tests; failed readiness restores the previous healthy release and reports its evidence.

### Issue 3 — Main governance and deployment gates

**Scope:** repository rulesets/branch protection, `.github/workflows/ci.yml`, `.github/workflows/quality.yml`, `.github/workflows/supply-chain.yml`, auto-merge workflow or settings, deployment concurrency.

- [ ] Inspect existing rulesets and status-check names before changing repository settings; document the resolved policy in the operations guide.
- [ ] Require CI, quality, and supply-chain checks for `main`, prohibit force pushes, and permit automatic merge only after those exact-SHA gates succeed. Do not require routine human approval under the user’s authorization.
- [ ] Add deployment concurrency serialization and stale-deploy cancellation/ordering rules so older artifacts cannot replace newer `main` state.
- [ ] Verify workflow permissions are minimal and that forks/PRs cannot access production secrets.

**Acceptance:** A PR cannot auto-merge with any required gate absent or failing; a newer deployment cannot be overwritten by an older SHA; no routine approval prompt blocks authorized work.

### Issue 4 — Private identity, roles, targeted approvals, and audit

**Scope:** `bridge/telegram/middleware.py`, `audit_log.py`, `bridge/telegram/panels/router.py`, SQLite migrations/repositories, configuration docs, focused tests.

- [ ] Enforce private-chat-only control-plane access and centralize user/chat/role authorization for commands and callbacks.
- [ ] Add owner/admin/operator/reader role checks with no implicit role grants; preserve read-only access separately from mutations.
- [ ] Store sensitive-action approvals bound to actor, action, target, expiry, nonce, and one-time consumption. Reject stale/replayed/target-mismatched callbacks.
- [ ] Record allow/deny/result with secret redaction and tamper-evidence appropriate to the existing storage model; do not log tokens or message payload secrets.
- [ ] Add denial tests for group chats, unauthorized roles, expired/replayed approvals, and target substitution.

**Acceptance:** Every administrative path uses the same authorization gate; permitted sensitive operations consume one matching short-lived approval and leave an auditable event.

### Issue 5 — Unified durable Task Center and resource-aware execution

**Scope:** `bridge/services/task_service.py`, `task_queue.py`, `bridge/services/resource_service.py`, Telegram task panels/screens, SQLite migrations only when needed, tests.

- [ ] Add source adapters for local tasks, GitHub Actions, and OpenCode sessions while preserving each source’s native status and identifier.
- [ ] Provide paginated task history/detail, real progress only, recent events/errors, and supported pause/resume/cancel/retry/queued-priority actions.
- [ ] Persist pause/checkpoint state and make callback/action handling idempotent across duplicate updates and process restarts.
- [ ] Enforce a hard global concurrency ceiling of three; reduce starts to zero under resource pressure and avoid killing active work solely due to pressure.
- [ ] Add quota-paused state with UTC reset scheduling and bounded backoff; no repeated failed provider requests while paused.

**Acceptance:** Queue recovery preserves task identity and order; duplicate actions do not execute twice; concurrency and resource ceilings hold under race/fault tests.

### Issue 6 — GitHub repository, Issues, PRs, branches, Actions, and releases UI

**Scope:** `github_ci.py`, `bridge/services/ci_service.py`, `bridge/services/workspace_service.py`, Telegram GitHub panels, tests/docs.

- [ ] Add authorized repository discovery and paginated views for files/diffs/commits/branches, Issues, PRs/checks, workflows/runs/jobs/logs/artifacts, and releases.
- [ ] Add narrowly scoped creation/update/cancel/rerun/dispatch/merge operations supported by the credential and repository rules; do not offer force-push or repository deletion.
- [ ] Show operation status and source links; redact secrets from logs/messages and handle API rate limits with ETag/backoff.
- [ ] Add contract tests for missing scopes, repository allowlists, rate limits, pagination, and unsuccessful API responses.

**Acceptance:** Read and write views reflect GitHub API state, and forbidden or unsupported operations fail closed with actionable status.

### Issue 7 — Safe server operations, monitoring, alerts, and backup policy

**Scope:** `resource_monitor.py`, `bridge/services/health_service.py`, `bridge/services/notification_service.py`, `systemd.py`, `maintenance/*`, Telegram server panels, backup docs/tests.

- [ ] Expose sampled CPU/RAM/swap/disk/load/uptime/network/process data with sample time and explicit unsupported values.
- [ ] Restrict service controls to a reviewed allowlist and operation matrix; provide logs/health and safe actions without arbitrary shell execution.
- [ ] Add independent watchdog notification delivery with deduplication and bounded retries; avoid notifying repeatedly for an unchanged condition.
- [ ] Define encrypted/off-host backup retention where a trusted destination exists; preserve SQLite online backup, manifest/checksum/integrity checks, and safe restore verification.
- [ ] Separate OS package maintenance from application deployment and record health/rollback outcomes for each.

**Acceptance:** Telegram presents only supported measurements/actions; resource checks can block new work; backup integrity/restore fixtures and notification dedup tests pass.

### Issue 8 — Schedules, agent capabilities, and end-to-end recovery

**Scope:** `bridge/services/schedule_service.py`, `bridge/services/agent_service.py`, task/source adapters, Telegram panels, `docs/opencode-bridge-2.0-execution-state.md`, relevant test suites.

- [ ] Unify scheduled jobs with the task center, enforce dependency/conflict policy, and preserve durable run history.
- [ ] Display only controls and cost/progress data actually exposed by OpenCode/provider APIs; unavailable controls must be clearly disabled or absent.
- [ ] Add end-to-end recovery coverage for restart, API quota, expired approval, webhook replay, partial deploy, rollback, and duplicate updates.
- [ ] Reconcile T50–T54 execution-state documentation against current main and evidence. Set longer soak requirements only for changes whose data/security/queue/performance risk justifies them.
- [ ] Perform final security/operational review and publish an evidence report with exact SHAs, Actions links, deployment/rollback status, and `NOT TESTED` items.

**Acceptance:** Recovery paths are deterministic and bounded; execution-state docs match evidence; no release/deploy is declared complete without its applicable CI and operational proof.

## Per-Issue Delivery Loop

For each issue in order: create a branch from the latest `main`; implement with focused regression coverage; push and open a PR linked to the issue; inspect all required GitHub Actions checks on the exact head SHA; repair failures and re-run; auto-merge only after gates pass; confirm the merge SHA and resulting main checks; close the issue only after acceptance evidence; delete only that completed task branch. Keep the plan branch and later implementation branches isolated. Do not advance past a blocked dependency or report a layer as verified without its evidence.
