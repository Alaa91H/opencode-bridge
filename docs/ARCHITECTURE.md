# Architecture

- Status: current for OpenCode Bridge 3.65.x
- Audience: maintainers and operators
- Related: [`AGENTS.md`](../AGENTS.md), [`DECISIONS.md`](DECISIONS.md), [`ADR 0001`](adr/0001-freebuff-cli-backend.md)

## Purpose

OpenCode Bridge turns a Telegram chat into the control surface for a locally
hosted development agent. Telegram is the interface; the bridge owns
authorization, durable task state, attachment staging, progress delivery and
backend selection. The actual agent runtime stays on the host.

## Runtime topology

```text
Telegram user
    │  update (text, file, callback)
    ▼
python-telegram-bot middleware / authorization
    │
    ├── command adapters ──► panels, schedules, queue, settings, /backend
    │
    └── TaskApplicationService ──► durable SQLite queue
                                      │
                                      ▼
                              TaskExecutionService
                                      │
                         ┌────────────┴────────────┐
                         ▼                         ▼
                  OpenCode backend          Freebuff backend
                  (default, loopback)       (opt-in, PTY)
                         │                         │
                         └────────────┬────────────┘
                                      ▼
                          progress reporter / final delivery
```

The process runs as a user-level systemd service. OpenCode listens on
`127.0.0.1`; the bridge never exposes the agent port publicly.

## Layers

### Domain

`bridge/domain/` contains backend-independent contracts:

- `agent/backend.py` defines the `AgentBackend` port.
- `agent/events.py` defines normalized assistant/tool/completion events.
- `agent/errors.py` defines user-safe backend errors.
- `agent/capabilities.py` describes sharing, model selection and cancellation
  capabilities.
- `tasks/` contains durable task, retry and policy models.
- `policies/` contains owner, request and execution policies.

The domain layer must not import Telegram or host-specific adapters.

### Services

`bridge/services/` orchestrates use cases. The important seams are:

| Service | Role |
| --- | --- |
| `AgentService` | Session lifecycle, model state and backend delegation |
| `BackendRouter` | Per-owner selection of OpenCode/Freebuff/auto |
| `TaskApplicationService` | Submission, queue ordering, cancellation, retries |
| `TaskExecutionService` | One execution attempt, progress events, final response |
| `ScheduleService` | Durable recurring and one-time schedules |
| `GitHubAdminService` | Allow-listed repository and release operations |
| `AdminBackups` | SQLite snapshots, encrypted backups and retention |
| `ToolManager` | Fixed host-tool catalogue and capability policy |

### Infrastructure

- `bridge/infrastructure/opencode/` wraps the loopback OpenCode HTTP API.
- `bridge/infrastructure/freebuff/` contains the CLI probe, PTY runner, ANSI
  screen model, chat-store reader, extractor and backend client.
- `bridge/infrastructure/database/` owns SQLite connections, migrations and
  repositories.
- `bridge/infrastructure/sandbox/` contains isolated build/test execution used by
  explicitly authorized workflows.

### Telegram

`bridge/telegram/` adapts updates to services:

- `app.py` registers commands and handlers.
- `commands/` contains command adapters, including `/backend`.
- `control_ui.py` and `panels` provide owner-only screens.
- `rendering/` turns Markdown and news digests into bounded Telegram HTML.
- `middleware.py` enforces authorization and rejects unsupported update shapes.

## Task lifecycle

1. A text message, caption or staged attachment becomes an owner-scoped task.
2. Attachments are validated, hashed, stored and associated with the task; the
   agent receives local paths, never arbitrary remote URLs as executable input.
3. `TaskApplicationService` admits or queues the task according to owner and host
   pressure.
4. `TaskExecutionService` creates one editable status message and starts the
   selected backend.
5. `AgentService` resolves a backend once per operation. OpenCode resolves to
   `None` so legacy behavior is unchanged; Freebuff resolves to an
   `AgentBackend` instance.
6. Progress events are normalized and consumed by `progress_reporter`; the
   Telegram message is edited in place.
7. The final answer is sanitized, bounded and delivered; scratch files and
   Freebuff prompt/attachment staging are deleted.
8. Terminal one-off tasks are removed from the durable queue according to the
   recording policy; recurring and scheduled work retains its state.

## Backend abstraction

### OpenCode (default)

`AGENT_BACKEND=opencode` or an unset per-owner preference returns `None` from
`BackendRouter.resolve()`. `AgentService` then calls the existing
`OpenCodeClient` methods and the legacy `sessions` table. This is the
compatibility guarantee: the abstraction adds a branch, it does not rewrite the
OpenCode path.

### Freebuff (opt-in)

Freebuff is an interactive TUI with no supported headless transport. The adapter
therefore:

1. Stages the full instruction in
   `<FREEBUFF_WORKSPACE>/.bridge/prompts/<run>.md` with mode `0600`.
2. Starts `freebuff --cwd <workspace>` in a PTY, adding `--continue <chat-id>`
   only for a validated stored session.
3. Injects one short line pointing at the staged file, avoiding multi-line paste
   and keeping prompt text out of `argv`.
4. Reads the terminal through a bounded ANSI screen model to detect login, busy,
   quota and idle states.
5. Extracts the final answer from the run's own
   `projects/*/chats/*/chat-messages.json`, selected by the unique staged prompt
   marker, then falls back to `/export`.
6. Kills the process group on cancellation or timeout and removes staging files.

`log.jsonl` is never opened: it has been observed to contain a plaintext account
key after authentication errors.

### Routing and storage

- `AGENT_BACKEND` is the server default: `opencode`, `freebuff` or `auto`.
- A per-owner preference in `backend_preferences` overrides the default.
- `auto` selects Freebuff only when the binary is installed, the version is
  supported and a credentials file is present; otherwise it stays on OpenCode.
- `backend_sessions` is keyed by `(owner, backend, workspace)`; it is separate
  from the legacy `sessions` schema.
- Freebuff runs are keyed by `(owner, session)` and chat selection is filtered by
  the unique prompt marker, preventing two concurrent owners from consuming each
  other's answer.

## Persistence

SQLite is the durable source of truth. The migration runner is idempotent and
adds tables such as `agent_tasks`, `scheduled_jobs`, `schedules`,
`schedule_occurrences`, `attachments`, `task_outputs`, `task_events`,
`agent_sessions`, `idempotency_records`, `provider_quota_pauses`, `reminders`,
`managed_repos`, `backend_sessions` and `backend_preferences`.

Rules:

- Migrations are additive and restart-safe.
- Queue claims and state transitions are transactional.
- Runtime artifacts (database, attachments, backups, Freebuff staging) live under
  ignored runtime directories, never in the repository.
- No migration requires deleting existing sessions or tasks.

## Progress and delivery

The default UX is one message: a status card is created after the instruction,
edited during execution, then replaced by the final answer. Raw tool payloads,
reasoning and search dumps are not posted. Output is ANSI-stripped, redacted for
known token shapes, length-bounded and rendered once before Telegram delivery.
Large results are delivered as files or paginated HTML.

## Security boundaries

| Boundary | Control |
| --- | --- |
| Telegram identity | Allow-list plus explicit owner authorization; group callbacks rejected |
| Prompt input | Guard patterns, untrusted attachment labeling, no execution of embedded instructions |
| OpenCode | Loopback-only HTTP, provider credentials loaded from local environment |
| Freebuff | Minimal child environment, workspace-confined cwd, no secrets in argv, fixed binary probe |
| Freebuff storage | Never read `log.jsonl`; parse only size-bounded `chat-messages.json` |
| Processes | Process-group termination, timeouts, bounded output |
| Secrets | Never committed, logged, placed in Telegram or copied into Freebuff prompts |
| Deployment | User-level systemd, `NoNewPrivileges`, safe kernel hardening, no build on host |

## CI and verification

GitHub Actions is authoritative:

- **Build and Release** runs on every branch/tag: quality gate, syntax checks,
  full unit suite, SBOM generation and packaging.
- **Python Compatibility** runs the full suite on Python 3.12, 3.13 and 3.14.
- **Verified version release** tags a version-bumped `main` only after both gates
  succeed, then dispatches the tagged build that publishes the GitHub Release.

Local Windows runs exercise the parser/store/router and skip PTY tests because
`termios` is unavailable. Linux CI, and a Linux smoke test, must cover the real
PTY path.

## Known constraints

- Freebuff has no official headless/ACP transport yet; the PTY harness is an
  adapter that must fail closed when the TUI changes.
- Real Freebuff account, quota and TUI behavior cannot be validated in the
  Windows development checkout. Production validation requires a Linux host
  with `freebuff login` under the service user.
- The full test suite currently has pre-existing Windows-only failures around
  symlinks, POSIX permissions and timezone data; CI Linux is the release gate.
- The internal ANSI screen model is deliberately not a full VT emulator and does
  not add `pyte` to the locked runtime dependency set.
