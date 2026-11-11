# Project Status

- Date: 2026-11-11
- Version under release: **3.65.0**
- Default backend: **OpenCode**
- Optional backend: **Freebuff CLI** (owner opt-in)
- Long-lived branch: `main`

## Current state

The Freebuff CLI backend is implemented, tested with a simulated TUI, and
merged into `main` with OpenCode behavior preserved as the default. The release
candidate includes the backend abstraction, PTY harness, chat-store extraction,
per-owner routing, additive persistence, security controls, operator guides and
regression tests.

The 3.65.0 release is being finalized through the repository's verified release
workflow: green `main` build and Python compatibility gates, immutable tag,
tagged build and GitHub Release. CI evidence is recorded in
[`SESSION_LOG.md`](SESSION_LOG.md) and the GitHub Actions run history.

## Delivered capabilities

| Area | State |
| --- | --- |
| Arabic Telegram command surface | Stable |
| Durable task queue, schedules, reminders | Stable |
| Attachments, media staging and output delivery | Stable |
| OpenCode backend and model management | Stable, default |
| Freebuff backend and per-owner `/backend` | New in 3.65.0 |
| Owner isolation and cancellation routing | New in 3.65.0 |
| Quality gate, CI pinning and release workflow | Maintained |
| Architecture/status/roadmap/session documentation | Consolidated in 3.65.0 |

## Verification evidence

Local verification on the Windows development checkout:

| Check | Result |
| --- | --- |
| `tests/freebuff` | 48 passed, 2 skipped (PTY is Unix-only) |
| Core affected tests | 27 passed |
| Linux PTY smoke via WSL (simulated TUI) | 1 passed |
| `compileall` | Passed |
| `ruff` on touched/new files | Passed |
| Full local suite | 1379 tests, 13 failures, 179 errors, 14 skipped |
| Original `HEAD` baseline before the change | 1332 tests, 13 failures, 179 errors, 12 skipped |

The full-suite failures are the same pre-existing Windows environment failures
(symlink privileges, POSIX permission checks, timezone data and host-service
assumptions), not regressions from the Freebuff work. GitHub Actions on Linux is
the release gate.

## Security posture

- Freebuff is disabled by default; owners opt in with `/backend freebuff` or
  `AGENT_BACKEND=freebuff`.
- `log.jsonl` is never read, logged, copied or delivered.
- Freebuff receives a minimal environment; Telegram and provider secrets are not
  passed to the CLI.
- Prompt text is staged in a `0600` file, not in `argv`.
- Output is ANSI-stripped, redacted and bounded before Telegram delivery.
- Backend sessions are owner-scoped and additive; no legacy session is deleted.
- Cancellation targets the process group and the correct owner/run.

## Known limitations

1. **No real Freebuff account was exercised locally.** The PTY path passed
   against a deterministic fake TUI. Production validation must confirm login,
   quota handling, `--continue`, cancellation and the current CLI screen shape.
2. **Freebuff remains an unofficial TUI integration.** It can break when the
   upstream TUI changes; the version gate and fail-closed behavior are the
   mitigations. ACP can replace the transport behind the same port when merged
   upstream.
3. **Windows test environment is not the release environment.** Full Linux CI is
   required before deployment.
4. **Model selection is conservative.** Freebuff model writes are disabled by
   default and `/model` displays the configured/allowed view rather than editing
   vendor state.
5. **Host deployment is separate from GitHub release publication.** A published
   release does not deploy itself; the normal `scripts/deploy.sh v3.65.0` flow is
   still required on the server.

## Repository hygiene

The remote previously carried 27 non-main branches. The two Dependabot update
branches were merged normally; the other 25 branches were superseded by newer
`main` implementations and were archived with `-s ours` merge commits so their
commits remain in history without replacing the current tree. Remote deletion
follows the successful push of the consolidated `main`. No unique commit is lost
from reachable history.

## Documentation map

Start at [`docs/README.md`](README.md). Key pages:

- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`ROADMAP.md`](ROADMAP.md)
- [`DECISIONS.md`](DECISIONS.md)
- [`SESSION_LOG.md`](SESSION_LOG.md)
- [`freebuff-integration-ar.md`](freebuff-integration-ar.md)
- [`freebuff-integration-en.md`](freebuff-integration-en.md)
- [`../AGENTS.md`](../AGENTS.md)
