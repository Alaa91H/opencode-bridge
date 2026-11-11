# Decision Log

Accepted decisions that shape the project. Detailed rationale for the Freebuff
integration is in [`ADR 0001`](adr/0001-freebuff-cli-backend.md).

| Date | Decision | Rationale | Status |
| --- | --- | --- | --- |
| 2026-10-26 | Keep OpenCode as the default agent backend | Existing conversations, sessions and provider behavior must remain unchanged for all owners who do not opt in | Accepted |
| 2026-11-11 | Add an `AgentBackend` port and `BackendRouter` | Decouple Telegram/task orchestration from one vendor and allow per-owner selection without rewriting legacy code | Accepted |
| 2026-11-11 | Implement Freebuff as a PTY harness, not an SDK integration | Freebuff CLI has no supported headless/ACP transport; the paid Codebuff SDK does not accept the free device token | Accepted, revisit when upstream ACP ships |
| 2026-11-11 | Use staged prompt files and a unique marker | Avoid multi-line TUI paste problems, keep prompts out of `argv`, and identify the correct concurrent chat record | Accepted |
| 2026-11-11 | Do not add `pyte` | The dependency policy locks the production runtime to three packages; a bounded internal ANSI screen model is sufficient for state detection and fails closed | Accepted |
| 2026-11-11 | Store Freebuff sessions in additive tables | Keep the legacy `sessions` schema and OpenCode code paths untouched; migration remains reversible and idempotent | Accepted |
| 2026-11-11 | Scope every run and event stream by owner | Concurrent owners must not read, abort or consume each other's Freebuff chat | Accepted |
| 2026-11-11 | Disable Freebuff model writes by default | Editing vendor settings is optional and only enabled explicitly with `FREEBUFF_MODEL_WRITE=1` | Accepted |
| 2026-11-11 | Archive superseded branches with `-s ours` | `main` already contains newer implementations; this records merge ancestry and preserves commits without reintroducing old trees | Accepted for this consolidation |
| 2026-11-11 | Consolidate stale T-series and 2.0 planning docs | The code/tests are the source of truth; keeping contradictory stage notes harms operators and agents | Accepted |
| 2026-11-11 | Treat Linux GitHub Actions as the release gate | Windows lacks PTY and has environment-specific symlink/permission/timezone failures | Accepted |

## Consequences to preserve

1. OpenCode behavior is the compatibility baseline; new work must not require
   changing legacy sessions.
2. Freebuff failures are explicit: unsupported version, missing login, unknown
   TUI, missing chat store or timeout all produce a user-safe error.
3. New documentation should describe the current system, not resurrect removed
   stage plans. Historical detail remains in Git history.
4. A released tag is immutable. Fixes after a release receive a new version.
