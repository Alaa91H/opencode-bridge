# ADR 0001: Freebuff CLI as a second agent backend

- Status: Accepted
- Date: 2026-11-11
- Owners: OpenCode Bridge maintainers

## Context

OpenCode Bridge is built around a loopback OpenCode HTTP server. The owner asked
for the same Telegram control plane to drive the **Freebuff CLI** as well.

At the time of this decision Freebuff has no supported programmatic transport:

- `freebuff` is an interactive terminal UI. `--print`, `--json`, `--headless`
  and piped prompts are unsupported (upstream issue CodebuffAI/freebuff#947 is
  still open).
- The ACP proposal (CodebuffAI/freebuff#1611) and the "initial prompt argument"
  pull request (#1636) are open, not released.
- `@codebuff/sdk` requires a paid Codebuff API key (`cb_...`). The Freebuff
  device-OAuth token is not accepted by the SDK; community experiments report
  Payment Required / Forbidden.

The CLI does expose stable user-facing pieces we can build on: `--cwd`,
`--continue <chat-id>`, `freebuff login`, slash commands such as `/model`,
`/new`, `/export` (Markdown or JSON), and a local conversation store under
`~/.config/manicode/projects/*/chats/chat-messages.json`.

## Decision

1. Introduce an `AgentBackend` domain contract and keep **OpenCode as the
   default**. `BackendRouter.resolve()` returns `None` for OpenCode so every
   legacy path is unchanged.
2. Implement `FreebuffBackend` as a **PTY harness**:
   - stage the full instruction in `<workspace>/.bridge/prompts/<run>.md` and
     inject one short line pointing at it, avoiding multi-line paste issues and
     keeping prompt text out of `argv`;
   - spawn `freebuff --cwd <workspace>` (plus `--continue <chat-id>` when a
     conversation exists) with a minimal environment;
   - read the terminal through a small internal ANSI screen model, detect
     login/busy/error state, and treat a stable, non-busy screen as completion;
   - extract the answer from the newest local chat record, with `/export` JSON
     as a bounded fallback; fail closed if neither yields text.
3. Persist conversations in a dedicated `backend_sessions` table keyed by
   `(owner, backend, workspace)`, and per-owner selection in
   `backend_preferences`. The legacy `sessions` table is untouched.
4. Expose `/backend opencode|freebuff|auto`. `auto` selects Freebuff only when
   the binary is installed, the version is supported, and a credentials file
   exists; otherwise it stays on OpenCode.
5. Keep the feature opt-in: `AGENT_BACKEND=opencode` by default.

## Consequences

- No new runtime dependency. `pyte` was considered, but
  `tests/test_dependency_policy.py` intentionally pins the production venv to
  exactly three packages and the host forbids installing more. The internal
  screen model is bounded and sufficient for state detection; it is not a full
  VT emulator and must fail closed when the TUI becomes unrecognisable.
- The integration depends on an unofficial TUI shape. Mitigations: minimum
  version gate, capability probe, explicit reasons surfaced in `/backend`,
  clear Arabic errors, and one-line rollback.
- Freebuff runs with the service user's permissions and executes tools without
  an approval gate. The bridge therefore confines `cwd` to the configured
  workspace, passes a minimal environment, never enables `--trust-agents`
  unless the owner opts in, and relies on the existing prompt guard plus the
  host's systemd isolation.
- `log.jsonl` may contain a plaintext account key after authentication errors.
  It is never read, copied, logged or delivered. Only
  `chat-messages.json` is parsed, defensively.
- When upstream ships ACP, a `FreebuffAcpTransport` can be added behind the same
  `AgentBackend` contract without changing Telegram, the queue or storage.

## Alternatives considered

- **Wait for official headless/ACP.** Rejected for now: the owner asked for the
  integration, and the harness is usable today behind an opt-in flag.
- **Reuse the Freebuff token with `@codebuff/sdk`.** Rejected: not supported,
  and it currently fails with Payment Required/Forbidden.
- **Scrape the TUI only.** Rejected as the primary extraction path: screen text
  includes ads, tool output and redraw noise. The chat store is the primary
  source; the screen is used for state, not for the final answer.
- **Write the CLI's model setting by default.** Rejected: editing vendor
  settings is only enabled with `FREEBUFF_MODEL_WRITE=1`.
