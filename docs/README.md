# Documentation Index

OpenCode Bridge documentation for version 3.65.x. Start with the root
[`README.md`](../README.md) for the product overview and deployment entry point.

## Start here

- [`../README.md`](../README.md) — capabilities, configuration and deployment.
- [`../AGENTS.md`](../AGENTS.md) — rules for coding agents working on this repo.
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — current release state, evidence and
  known limitations.
- [`SESSION_LOG.md`](SESSION_LOG.md) — recent implementation and verification
  history.

## Architecture and decisions

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — runtime topology, layers, task
  lifecycle, backend abstraction, persistence and security boundaries.
- [`DECISIONS.md`](DECISIONS.md) — accepted decisions and consequences.
- [`adr/0001-freebuff-cli-backend.md`](adr/0001-freebuff-cli-backend.md) — full
  ADR for the optional Freebuff backend.
- [`ROADMAP.md`](ROADMAP.md) — current, next and later work.

## Agent backends

- [`freebuff-integration-ar.md`](freebuff-integration-ar.md) — Arabic setup,
  security and rollback guide.
- [`freebuff-integration-en.md`](freebuff-integration-en.md) — English quick
  guide.

## Operations

- [`../DEPLOYMENT_AR.md`](../DEPLOYMENT_AR.md) — deployment procedure.
- [`../RELEASE_PROCESS_AR.md`](../RELEASE_PROCESS_AR.md) — release workflow.
- [`../SECURITY.md`](../SECURITY.md) — vulnerability reporting and threat model.
- [`durable-task-resume.md`](durable-task-resume.md) — durable task recovery.
- [`autopilot-control-guide-ar.md`](autopilot-control-guide-ar.md) — update
  policies and autopilot controls.
- [`emergency-update-stop-ar.md`](emergency-update-stop-ar.md) — emergency stop
  behavior.
- [`host-inventory-guide-ar.md`](host-inventory-guide-ar.md) — read-only host
  inventory.
- [`download-index-recovery-ar.md`](download-index-recovery-ar.md) — download
  index fail-closed recovery.
- [`midnight-download-retention-ar.md`](midnight-download-retention-ar.md) —
  optional midnight cache retention.
- [`offline-encrypted-backup-verification-ar.md`](offline-encrypted-backup-verification-ar.md)
  — encrypted backup restore checks.
- [`real-age-recovery-smoke-ar.md`](real-age-recovery-smoke-ar.md) — real `age`
  recovery smoke test.
- [`telegram-owner-authorization-ar.md`](telegram-owner-authorization-ar.md) —
  owner-only authorization.
- [`pinned-public-http-dial-ar.md`](pinned-public-http-dial-ar.md) — DNS-pinned
  public HTTP.
- [`update-ledger-filesystem-hardening-ar.md`](update-ledger-filesystem-hardening-ar.md)
  — update journal filesystem checks.
- [`zen-free-quota-recovery.md`](zen-free-quota-recovery.md) — OpenCode Zen
  quota recovery.
- [`security-review-2026-10-12-ar.md`](security-review-2026-10-12-ar.md) —
  historical security review; verify current code before relying on it.

## Contributing

- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — contribution and verification
  workflow.
- [`../CHANGELOG.md`](../CHANGELOG.md) — release history.
- [`../releases/v3.65.0.md`](../releases/v3.65.0.md) — current release notes.

## Removed documentation

The obsolete T01–T54 implementation notes and 2.0 planning/execution pages were
removed from the live tree because they described superseded designs and
conflicted with the current architecture. Their history remains available in Git
and in the repository history; use `ARCHITECTURE.md`, `PROJECT_STATUS.md` and
the tests as the current source of truth.
