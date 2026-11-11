# Project Status

- Date: 2026-11-11
- Current release: **3.65.1** (published 2026-11-11)
- Default backend: **OpenCode**
- Optional backend: **Freebuff CLI** (owner opt-in)
- Long-lived branch: `main`

## Current state

The Freebuff CLI backend is implemented, tested with a simulated TUI, and
merged into `main` with OpenCode behavior preserved as the default. The 3.65.0
feature baseline includes the backend abstraction, PTY harness, chat-store
extraction, per-owner routing, additive persistence, security controls,
operator guides and regression tests. The 3.65.1 patch adds host-specific
systemd hardening detection and rollback unit regeneration.

The 3.65.0 release was published on 2026-11-11 through the repository's
verified release workflow. The annotated tag `v3.65.0` (tag object `06dc280`)
points to release commit `ca58eaf`. The Build and Release and Python
Compatibility gates passed on `main`, the version transition was verified, and
the tagged build published the GitHub Release with `opencode-bridge-v3.65.0.tar.gz`
and `SHA256SUMS`. See the [release](https://github.com/Alaa91H/opencode-bridge/releases/tag/v3.65.0)
and [`SESSION_LOG.md`](SESSION_LOG.md).

The 3.65.1 patch was published on 2026-11-11. The annotated tag `v3.65.1` (tag
object `c90a187`) points to release commit `3baa230`. The `main` Build and
Release and Python Compatibility gates passed, the verified release workflow
created the tag, and the tagged build published
`opencode-bridge-v3.65.1.tar.gz` and `SHA256SUMS`. The server was deployed to
`v3.65.1` at `2026-11-11T10:11:34Z` through `scripts/deploy.sh`.

## Delivered capabilities

| Area | State |
| --- | --- |
| Arabic Telegram command surface | Stable |
| Durable task queue, schedules, reminders | Stable |
| Attachments, media staging and output delivery | Stable |
| OpenCode backend and model management | Stable, default |
| Freebuff backend and per-owner `/backend` | New in 3.65.0 |
| Owner isolation and cancellation routing | New in 3.65.0 |
| Container-safe systemd deployment | New in 3.65.1 |
| Quality gate, CI pinning and release workflow | Maintained |
| Architecture/status/roadmap/session documentation | Consolidated in 3.65.0 |

## 3.65.1 deployment patch

`systemd.py` probes each optional hardening directive in a transient user
service and generates `20-hardening-compat.conf` only for directives the host
rejects. A full-capability host receives no file, and a stale fallback is
removed. `NoNewPrivileges=yes` remains enabled. `scripts/deploy.sh` regenerates
units for the restored release during rollback. See [`SESSION_LOG.md`](SESSION_LOG.md).

## Verification evidence

Local verification on the Windows development checkout:

| Check | Result |
| --- | --- |
| `tests/freebuff` | 48 passed, 2 skipped (PTY is Unix-only) |
| Container-safe deployment tests | 28 passed on Linux |
| Core affected tests | 27 passed |
| Linux PTY smoke via WSL (simulated TUI) | 1 passed |
| `compileall` | Passed |
| `ruff` on touched/new files | Passed |
| Full local suite | 1379 tests, 13 failures, 179 errors, 14 skipped |
| Original `HEAD` baseline before the change | 1332 tests, 13 failures, 179 errors, 12 skipped |
| CI Build and Release (`5b81669`) | success (run 42995712863) |
| CI Python Compatibility (`5b81669`) | success (run 42995713078) |
| CI Build and Release (`ca58eaf`) | success (run 42996065610) |
| CI Python Compatibility (`ca58eaf`) | success (run 42996065550) |
| Verified version release | success (run 42996241405) |
| Tagged Build and Release (`v3.65.0`) | success (run 42996264181) |
| GitHub Release | published 2026-11-11 with archive and checksums |
| CI Build and Release (`3baa230`) | success (run 43001010328) |
| CI Python Compatibility (`3baa230`) | success (run 43001011188) |
| Verified version release (`v3.65.1`) | success (run 43001161674) |
| Tagged Build and Release (`v3.65.1`) | success (run 43001191347) |
| GitHub Release (`v3.65.1`) | published 2026-11-11T10:10:28Z with archive and checksums |
| Server deployment (`v3.65.1`) | active at 2026-11-11T10:11:34Z; health true, `NRestarts=0` |

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
   release does not deploy itself. The server was deployed to `v3.65.1` on
   2026-11-11; future releases still require `scripts/deploy.sh` on the host.

## Repository hygiene

The remote previously carried the two Dependabot update branches and 25
superseded feature/security branches. The Dependabot work was merged normally;
the other 25 branches were archived with `-s ours` merge commits so their commits
remain in history without replacing the current tree. All non-`main` remote
branches were deleted after the consolidated push. `main` is now the only remote
and local branch, and no unique commit was lost from reachable history.

## Documentation map

Start at [`docs/README.md`](README.md). Key pages:

- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`ROADMAP.md`](ROADMAP.md)
- [`DECISIONS.md`](DECISIONS.md)
- [`SESSION_LOG.md`](SESSION_LOG.md)
- [`freebuff-integration-ar.md`](freebuff-integration-ar.md)
- [`freebuff-integration-en.md`](freebuff-integration-en.md)
- [`../AGENTS.md`](../AGENTS.md)
