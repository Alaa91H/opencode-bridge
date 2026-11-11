# AGENTS.md

Instructions for coding agents working on **OpenCode Bridge**.

## Project

OpenCode Bridge is a Python 3.12+ Telegram control plane for a locally hosted
OpenCode agent. It persists conversations, queues development tasks, stages
Telegram attachments, streams progress into one editable status message, and
keeps the production host as an execution environment rather than a build host.

Read these first:

- `README.md` — operator overview and capabilities.
- `docs/ARCHITECTURE.md` — runtime topology, layers and data flow.
- `docs/PROJECT_STATUS.md` — current release state and known limitations.
- `docs/DECISIONS.md` — accepted decisions and links to ADRs.
- `docs/SESSION_LOG.md` — recent implementation and verification history.

## Non-negotiable invariants

1. **OpenCode remains the default backend.** `AGENT_BACKEND=opencode` must keep
   the existing OpenCode code paths behavior-compatible. Freebuff is opt-in.
2. **No secrets in source, arguments, logs, docs or Telegram.** Never read or
   print `.env`, tokens, private keys, `log.jsonl`, or credential files. The
   Freebuff chat store is the only Freebuff storage that may be parsed.
3. **Owner isolation.** Every session, preference, task, abort and event stream
   is scoped to the authenticated Telegram owner. Never accept a client-supplied
   owner id as authority.
4. **Additive persistence.** New SQLite tables and columns must be idempotent and
   must not require destructive migration of existing sessions or tasks.
5. **Fail closed.** Unsupported Freebuff versions, missing login, unknown TUI
   shapes and unreadable stores produce a clear error, not a guessed answer.
6. **Do not install or build on the production host.** The bridge executes
   already-prepared code; dependency installation is a deployment concern.
7. **Documentation is part of the change.** Update `CHANGELOG.md`,
   `docs/PROJECT_STATUS.md` and `docs/SESSION_LOG.md` for material behavior,
   verification or release changes.

## Layout

| Path | Responsibility |
| --- | --- |
| `bot.py` | Telegram composition root and command wiring |
| `bridge/domain/` | Backend contracts, events, errors, policies, task models |
| `bridge/services/` | Agent, task execution, routing, GitHub, backups, scheduling |
| `bridge/infrastructure/` | OpenCode, Freebuff, database, sandbox and host adapters |
| `bridge/telegram/` | Commands, panels, renderers, middleware and delivery |
| `bridge/workers/` | Background workers |
| `maintenance/`, `scripts/` | Deployment, verification, release and host operations |
| `tests/` | Unit and integration tests; `tests/freebuff/` covers the PTY harness |
| `docs/` | Architecture, status, decisions, operations and ADRs |

## Verification

On a Windows checkout, use the project virtual environment because system Python
does not contain the runtime packages:

```bash
.venv/Scripts/python -m unittest discover -s tests/freebuff -t . -v
.venv/Scripts/python -m unittest tests.test_command_coverage tests.test_bot_import
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m compileall -q .
```

On Linux/CI:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
bash scripts/quality.sh
```

The PTY tests skip on Windows. GitHub Actions on Linux is the authoritative
platform for the complete suite. Do not describe a skipped test as a pass.

## Releases

1. Keep `VERSION`, `CHANGELOG.md` and `README.md` consistent.
2. Push a green `main`; the `Verified version release` workflow creates the
   immutable tag and dispatches the tagged build only after the main build and
   Python compatibility gates succeed.
3. Verify the published GitHub Release and the tagged build before declaring the
   release complete.
4. Do not force-push, rewrite released tags, or delete released history.

## Branch hygiene

`main` is the only long-lived branch. Feature branches are archived with a merge
commit before deletion; superseded branches are merged with `-s ours` only when
`main` already contains the newer implementation. This preserves their commits
as merge parents without overwriting the current tree.

## Style

- Follow the existing Arabic operator-facing text and English code identifiers.
- Keep changes focused; do not reformat unrelated files.
- Prefer the smallest correct change and add regression tests for defects.
- Use `unittest`, not `pytest`, for repository tests.
- Never add a dependency to the production lockfile without an explicit decision
  in `docs/DECISIONS.md` and a deployment plan.
